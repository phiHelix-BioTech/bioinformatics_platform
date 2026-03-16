"""Shared Turkish-cloud runner logic.

All three pipeline types (Nextflow, Snakemake, BioScript) share the same
lifecycle:
  1. Build a cloud-init user_data script specific to the pipeline type.
  2. Provision a VM via the provider factory (with fallback).
  3. Poll object storage for a completion marker written by the VM.
  4. Collect results from object storage.
  5. Terminate the VM (always, even on failure).

The completion protocol uses S3-compatible object storage (Huawei OBS or
Turkcell nDepo) as a rendezvous point:
  - VM writes  s3://<bucket>/completion/<job_id>/done   on success
  - VM writes  s3://<bucket>/completion/<job_id>/error  on failure (body = message)
  - Celery polls with HEAD requests every POLL_INTERVAL seconds.
"""
from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# How often to check for the completion marker (seconds)
POLL_INTERVAL: int = 30

# Maximum time to wait for a VM job (seconds).  Slightly under Celery
# soft_time_limit so the task can cleanly write status=failed.
JOB_TIMEOUT: int = 13_500  # 3 h 45 m

# File extensions to surface to the user
_KEEP_EXTS = {
    "html", "txt", "csv", "tsv", "json", "gz",
    "bam", "bai", "vcf", "bed", "bigwig", "bw",
    "narrowpeak", "broadpeak", "pdf", "png", "svg",
}


# ── Storage helpers ──────────────────────────────────────────────────────────

def _s3():
    """Return a boto3 S3 client pointed at the configured Turkish-cloud storage."""
    kwargs: dict = {
        "aws_access_key_id":     settings.AWS_ACCESS_KEY_ID or None,
        "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY or None,
        "region_name":           settings.AWS_REGION,
    }
    if settings.S3_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
    return boto3.client("s3", **kwargs)


def _marker_exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=settings.S3_BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def _read_marker(key: str) -> str:
    try:
        obj = _s3().get_object(Bucket=settings.S3_BUCKET, Key=key)
        return obj["Body"].read().decode(errors="replace")
    except ClientError:
        return ""


def collect_results(job_id: str, runtime: int, provider_name: str) -> dict:
    """List all output files under nf-output/{job_id}/ and return result dict."""
    output_prefix = f"nf-output/{job_id}/"
    paginator = _s3().get_paginator("list_objects_v2")
    files = []
    try:
        for page in paginator.paginate(Bucket=settings.S3_BUCKET, Prefix=output_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key.split("/")[-1]
                if not name or name.endswith("/"):
                    continue
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext not in _KEEP_EXTS:
                    continue
                files.append({
                    "name":        name,
                    "path":        f"s3://{settings.S3_BUCKET}/{key}",
                    "size_bytes":  obj["Size"],
                    "mime_type":   "application/octet-stream",
                    "description": "",
                })
    except ClientError as exc:
        logger.warning("[runner] result listing failed: %s", exc)

    return {
        "type":            "files",
        "files":           files,
        "instance_type":   provider_name,
        "runtime_seconds": runtime,
    }


# ── Polling ──────────────────────────────────────────────────────────────────

def poll_until_done(job_id: str, instance_id: str, provisioner) -> None:
    """Block until the VM signals completion or the timeout is reached.

    Raises RuntimeError on pipeline failure or timeout.
    """
    from app.services.vm_provisioner.base import VMProvisioner  # noqa: F401

    done_key  = f"completion/{job_id}/done"
    error_key = f"completion/{job_id}/error"
    start = time.time()

    while time.time() - start < JOB_TIMEOUT:
        if _marker_exists(done_key):
            logger.info("[runner] job %s completed successfully", job_id)
            return

        if _marker_exists(error_key):
            msg = _read_marker(error_key) or "unknown pipeline error"
            raise RuntimeError(f"Pipeline failed on VM (job={job_id}): {msg}")

        vm_status = provisioner.get_instance_status(instance_id)
        if vm_status == "error":
            raise RuntimeError(
                f"VM entered error state before pipeline completed (job={job_id})"
            )
        # VM stopped without writing a marker → treat as unexpected failure
        if vm_status == "stopped":
            # Give it one more check — marker may have been written just before stop
            time.sleep(5)
            if _marker_exists(done_key):
                return
            if _marker_exists(error_key):
                msg = _read_marker(error_key) or "unknown pipeline error"
                raise RuntimeError(f"Pipeline failed on VM (job={job_id}): {msg}")
            raise RuntimeError(
                f"VM stopped without writing a completion marker (job={job_id})"
            )

        elapsed = int(time.time() - start)
        logger.debug("[runner] job %s still running (%ds elapsed, vm=%s)",
                     job_id, elapsed, vm_status)
        time.sleep(POLL_INTERVAL)

    raise RuntimeError(
        f"Job {job_id} timed out after {JOB_TIMEOUT}s on provider "
        f"{provisioner.provider_name}"
    )


# ── Cloud-init template ───────────────────────────────────────────────────────

def build_user_data(
    pipeline_cmd: str,
    job_id: str,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Build a cloud-init bash script that runs ``pipeline_cmd`` and signals done/error.

    ``pipeline_cmd`` is the full shell command to execute (may span multiple
    lines using continuation backslashes).  The script:
      - Installs Docker + AWS CLI (idempotent)
      - Configures S3-compatible storage credentials
      - Runs ``pipeline_cmd``
      - Writes a done/error marker to object storage
      - Powers the VM off
    """
    s3_endpoint = settings.S3_ENDPOINT_URL or ""
    aws_endpoint_flag = (
        f"--endpoint-url {s3_endpoint}" if s3_endpoint else ""
    )

    env_block = ""
    if extra_env:
        env_block = "\n".join(f'export {k}="{v}"' for k, v in extra_env.items())

    # Build AWS CLI config so the cloud-init script can talk to Turkish OBS/nDepo
    script = f"""#!/bin/bash
set -o pipefail

# ── System setup ─────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io curl unzip

# Install AWS CLI v2 (used to write markers + upload artefacts)
if ! command -v aws &>/dev/null; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install
fi

# ── Credentials ──────────────────────────────────────────────────────────
mkdir -p ~/.aws
cat > ~/.aws/credentials <<'AWSCREDS'
[default]
aws_access_key_id = {settings.AWS_ACCESS_KEY_ID}
aws_secret_access_key = {settings.AWS_SECRET_ACCESS_KEY}
AWSCREDS
cat > ~/.aws/config <<'AWSCFG'
[default]
region = {settings.AWS_REGION}
AWSCFG

# ── Extra env ─────────────────────────────────────────────────────────────
{env_block}

# ── Pipeline ─────────────────────────────────────────────────────────────
export JOB_ID="{job_id}"
export S3_BUCKET="{settings.S3_BUCKET}"
export S3_ENDPOINT="{s3_endpoint}"
export AWS_ENDPOINT_FLAG="{aws_endpoint_flag}"
export WORKER_IMAGE="{settings.BIOSCRIPT_DOCKER_IMAGE}"

set +e
(
{pipeline_cmd}
) 2>&1 | tee /tmp/pipeline.log
PIPELINE_EXIT=$?
set -e

# Upload log
aws s3 cp /tmp/pipeline.log \
    "s3://${{S3_BUCKET}}/logs/${{JOB_ID}}/pipeline.log" \
    ${{AWS_ENDPOINT_FLAG}} || true

# ── Completion marker ─────────────────────────────────────────────────────
if [ "$PIPELINE_EXIT" -eq 0 ]; then
  echo "done" | aws s3 cp - \
      "s3://${{S3_BUCKET}}/completion/${{JOB_ID}}/done" \
      ${{AWS_ENDPOINT_FLAG}}
else
  echo "exit_code=$PIPELINE_EXIT" | aws s3 cp - \
      "s3://${{S3_BUCKET}}/completion/${{JOB_ID}}/error" \
      ${{AWS_ENDPOINT_FLAG}}
fi

# ── Self-terminate ────────────────────────────────────────────────────────
shutdown -h now
"""
    return script
