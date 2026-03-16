"""Nextflow runner on Turkish cloud VMs (Huawei / Turkcell / CloudSigma).

Spins up an ephemeral VM, runs nf-core pipelines with the local+Docker
executor (not AWS Batch), collects results from object storage, and
terminates the VM.
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.services.nextflow.base import NextflowRunner
from app.services.vm_provisioner.factory import get_provisioner_with_fallback
from app.services.vm_provisioner.runner import (
    JOB_TIMEOUT,
    build_user_data,
    collect_results,
    poll_until_done,
)

logger = logging.getLogger(__name__)


# Same version + param table as the AWS Batch runner
PIPELINE_VERSIONS: dict[str, str] = {
    "rnaseq":    "3.14.0",
    "sarek":     "3.4.4",
    "atacseq":   "2.1.2",
    "chipseq":   "2.0.0",
    "methylseq": "2.7.1",
    "ampliseq":  "2.11.0",
    "fetchngs":  "1.12.0",
}

PIPELINE_EXTRA_PARAMS: dict[str, str] = {
    "rnaseq":    "--genome GRCh38 --aligner star_salmon",
    "sarek":     "--genome GATK.GRCh38",
    "atacseq":   "--genome GRCh38",
    "chipseq":   "--genome GRCh38",
    "methylseq": "--genome GRCh38",
    "ampliseq":  "--FW_primer GTGYCAGCMGCCGCGGTAA --RV_primer GGACTACNVGGGTWTCTAAT",
}


def _samplesheet_csv(pid: str, input_uri: str, job_id: str, r2_uri: str = "") -> str:
    sample = f"sample_{job_id[:8]}"
    if pid in ("rnaseq", "methylseq"):
        return (
            "sample,fastq_1,fastq_2,strandedness\n"
            f"{sample},{input_uri},{r2_uri},auto\n"
        )
    if pid == "sarek":
        return (
            "patient,sex,status,sample,lane,fastq_1,fastq_2\n"
            f"patient1,XX,0,{sample},1,{input_uri},{r2_uri}\n"
        )
    return f"sample,fastq_1,fastq_2\n{sample},{input_uri},{r2_uri}\n"


def _tier_to_flavor(tier: str | None) -> str:
    _MAP = {"basic": "small", "standard": "standard", "premium": "large"}
    return _MAP.get((tier or "standard").lower(), "standard")


class TurkishCloudNextflowRunner(NextflowRunner):

    def run(
        self,
        pipeline_id: str,
        storage_key: str,
        file_type: str,
        job_id: str = "",
        storage_key_r2: str | None = None,
        workflow_config: dict | None = None,
    ) -> dict:
        start = time.time()
        pid     = pipeline_id.lower().removeprefix("nf-core/")
        version = PIPELINE_VERSIONS.get(pid, "main")
        extra   = PIPELINE_EXTRA_PARAMS.get(pid, "")

        endpoint_flag = (
            f"--endpoint-url {settings.S3_ENDPOINT_URL}"
            if settings.S3_ENDPOINT_URL else ""
        )
        input_uri  = f"s3://{settings.S3_BUCKET}/{storage_key}"
        r2_uri     = f"s3://{settings.S3_BUCKET}/{storage_key_r2}" if storage_key_r2 else ""
        output_uri = f"s3://{settings.S3_BUCKET}/nf-output/{job_id}/"
        work_uri   = f"s3://{settings.S3_BUCKET}/nf-work/{job_id}/"
        ss_uri     = f"s3://{settings.S3_BUCKET}/samplesheets/{job_id}/samplesheet.csv"

        csv = _samplesheet_csv(pid, input_uri, job_id, r2_uri)

        pipeline_cmd = f"""
# Write samplesheet and upload
cat > /tmp/samplesheet.csv <<'SAMPLESHEET'
{csv}
SAMPLESHEET
aws s3 cp /tmp/samplesheet.csv "{ss_uri}" {endpoint_flag}

# Pull worker image
docker pull {settings.BIOSCRIPT_DOCKER_IMAGE}

# Run Nextflow inside Docker with local executor + Docker for each step
docker run --rm \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  -v /tmp:/tmp \\
  -e NXF_WORK="{work_uri}" \\
  -e AWS_ACCESS_KEY_ID="{settings.AWS_ACCESS_KEY_ID}" \\
  -e AWS_SECRET_ACCESS_KEY="{settings.AWS_SECRET_ACCESS_KEY}" \\
  -e NXF_OPTS="-Xms512m -Xmx2g" \\
  {settings.BIOSCRIPT_DOCKER_IMAGE} \\
  nextflow run "nf-core/{pid}" \\
    -r {version} \\
    --input "{ss_uri}" \\
    --outdir "{output_uri}" \\
    -work-dir "{work_uri}" \\
    -profile docker \\
    {extra}
"""

        flavor = _tier_to_flavor(
            (workflow_config or {}).get("tier") or
            getattr(settings, "DEFAULT_VM_FLAVOR", "standard")
        )

        logger.info("[nf/turkishcloud] job=%s pipeline=%s flavor=%s", job_id, pid, flavor)

        user_data = build_user_data(pipeline_cmd, job_id)
        provisioner, instance_id = get_provisioner_with_fallback(job_id, user_data, flavor)

        try:
            poll_until_done(job_id, instance_id, provisioner)
        finally:
            provisioner.terminate_instance(instance_id)

        runtime = int(time.time() - start)
        return collect_results(job_id, runtime, provisioner.provider_name)
