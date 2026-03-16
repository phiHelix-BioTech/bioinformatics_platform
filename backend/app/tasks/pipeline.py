"""
Celery pipeline tasks.

Uses psycopg2 (sync) for DB access — Celery runs in a sync context.
DATABASE_URL in the worker env must use the postgresql+psycopg2 scheme
(docker-compose sets this automatically for the worker service).
"""
import hashlib
import logging
import random
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from app.celery_app import celery_app
from app.config import settings
from app.services.assessment.base import get_assessment_runner
from app.services.bioscript.base import get_bioscript_runner
from app.services.custom.base import get_custom_runner
from app.services.ec2.base import get_ec2_backend
from app.services.nextflow.base import get_nextflow_runner
from app.services.snakemake.base import get_snakemake_runner
from app.services.storage.base import get_storage_backend
from app.services.log_streamer import append_log
from app.services.email import send_job_notification

logger = logging.getLogger(__name__)

# Build a sync DATABASE_URL — swap asyncpg driver for psycopg2
_sync_url = settings.DATABASE_URL.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
).replace(
    "postgresql://", "postgresql+psycopg2://"
)

_engine = create_engine(_sync_url, pool_pre_ping=True)


def _update_job(job_id: str, **kwargs) -> None:
    kwargs["updated_at"] = datetime.now(timezone.utc)
    set_clauses = ", ".join(f"{k} = :{k}" for k in kwargs)
    with _engine.begin() as conn:
        conn.execute(
            text(f"UPDATE jobs SET {set_clauses} WHERE id = :job_id"),
            {"job_id": job_id, **kwargs},
        )


def _get_user_email(user_id: str) -> str | None:
    """Return the email address for a user_id, or None if not found."""
    if not user_id:
        return None
    try:
        with _engine.connect() as conn:
            row = conn.execute(
                text("SELECT email FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).mappings().one_or_none()
        return row["email"] if row else None
    except Exception:
        return None


def _get_job(job_id: str) -> dict:
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM jobs WHERE id = :job_id"),
            {"job_id": job_id},
        ).mappings().one()
    return dict(row)


# ── Generic pipeline task ─────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="app.tasks.pipeline.run_pipeline",
    # Soft limit raises SoftTimeLimitExceeded inside the running task so the
    # except block can write status="failed" before the hard SIGKILL fires.
    time_limit=14400,       # 4 h hard kill
    soft_time_limit=13800,  # 3 h 50 m — gives ~10 min for DB write + email
)
def run_pipeline(self, job_id: str) -> dict:
    """Route to the correct runner based on the job's pipeline_id."""
    logger.info("[pipeline] Starting job %s", job_id)

    try:
        # Retry initial DB read to survive momentary connection blips
        # (e.g., DB restart, network hiccup).  pool_pre_ping handles stale
        # pool connections but not complete unavailability.
        _last_db_exc: Exception | None = None
        for _attempt in range(3):
            try:
                job = _get_job(job_id)
                break
            except Exception as _db_exc:
                _last_db_exc = _db_exc
                if _attempt < 2:
                    logger.warning(
                        "[pipeline] DB read failed (attempt %d/3): %s — retrying",
                        _attempt + 1,
                        _db_exc,
                    )
                    time.sleep(2 ** _attempt)
        else:
            raise _last_db_exc  # type: ignore[misc]

        pipeline_id: str | None = job.get("pipeline_id")
        append_log(job_id, f"Job {job_id} started — pipeline: {pipeline_id or 'unknown'}")
        append_log(job_id, f"File type: {job.get('file_type', 'unknown')}  tier: {job.get('tier', 'unknown')}")

        # Stage 1: start EC2 (skipped when using real cloud runners — they manage
        # their own compute via AWS Batch)
        use_real_nextflow  = settings.NEXTFLOW_BACKEND  == "awsbatch"
        use_real_snakemake = settings.SNAKEMAKE_BACKEND == "awsbatch"
        use_real_bioscript = settings.BIOSCRIPT_BACKEND == "awsbatch"
        use_real_custom    = settings.CUSTOM_BACKEND     == "awsbatch"

        if not use_real_nextflow and not use_real_snakemake and not use_real_bioscript and not use_real_custom:
            _update_job(job_id, status="running", stage="ec2_starting")
            append_log(job_id, "Stage: ec2_starting — spinning up compute instance")
            logger.info("[pipeline] %s → ec2_starting", job_id)
            ec2 = get_ec2_backend()
            instance_id = ec2.spawn_instance(job["tier"])
            append_log(job_id, f"Instance ready: {instance_id}")
            logger.info("[pipeline] %s instance spawned: %s", job_id, instance_id)
        else:
            instance_id = None
            _update_job(job_id, status="running", stage="pipeline_running")
            append_log(job_id, "Stage: pipeline_running — submitting to AWS Batch")
            logger.info("[pipeline] %s → pipeline_running (real runner, skipping EC2)", job_id)

        if pipeline_id and pipeline_id.startswith("custom-"):
            # ── Custom Linux pipeline path ─────────────────────────────────
            tool = pipeline_id[len("custom-"):]
            _update_job(job_id, instance_id=instance_id, stage="pipeline_running")
            append_log(job_id, f"Stage: pipeline_running — running custom tool: {tool}")
            logger.info("[pipeline] %s → custom_running (tool=%s)", job_id, tool)

            custom = get_custom_runner()
            result = custom.run(
                tool,
                job["storage_key"],
                job["file_type"],
                job_id=job_id,
                storage_key_r2=job.get("storage_key_r2"),
            )
            append_log(job_id, f"Custom tool {tool} completed — {len(result.get('files', []))} output file(s)")
            logger.info("[pipeline] %s custom pipeline done (tool=%s)", job_id, tool)

        elif pipeline_id == "mixed":
            # ── Cross-framework path (Nextflow → Snakemake) ───────────────
            def _seed(key: str) -> random.Random:
                digest = hashlib.md5(key.encode()).hexdigest()
                return random.Random(int(digest[:8], 16))

            rng = _seed(job["storage_key"])
            sample = f"SAMPLE{rng.randint(1, 9)}"
            mixed_start = time.time()

            # Phase 1: Nextflow alignment / processing
            _update_job(job_id, instance_id=instance_id, stage="pipeline_running")
            logger.info("[pipeline] %s → mixed/pipeline_running (Nextflow phase)", job_id)
            time.sleep(rng.uniform(4.0, 8.0))
            nf_files = [
                {
                    "name": f"{sample}.sorted.bam",
                    "path": f"nextflow/align/{sample}.sorted.bam",
                    "size_bytes": rng.randint(200_000_000, 2_000_000_000),
                    "mime_type": "application/octet-stream",
                    "description": "[Nextflow] Sorted alignment (BAM)",
                },
                {
                    "name": f"{sample}.sorted.bam.bai",
                    "path": f"nextflow/align/{sample}.sorted.bam.bai",
                    "size_bytes": rng.randint(100_000, 500_000),
                    "mime_type": "application/octet-stream",
                    "description": "[Nextflow] BAM index",
                },
                {
                    "name": "nextflow_multiqc_report.html",
                    "path": "nextflow/multiqc/multiqc_report.html",
                    "size_bytes": rng.randint(2_000_000, 8_000_000),
                    "mime_type": "text/html",
                    "description": "[Nextflow] MultiQC report",
                },
                {
                    "name": "execution_report.html",
                    "path": "nextflow/pipeline_info/execution_report.html",
                    "size_bytes": rng.randint(500_000, 2_000_000),
                    "mime_type": "text/html",
                    "description": "[Nextflow] Execution report",
                },
            ]

            # Phase 2: Snakemake downstream analysis
            _update_job(job_id, instance_id=instance_id, stage="snakemake_running")
            logger.info("[pipeline] %s → mixed/snakemake_running (Snakemake phase)", job_id)
            time.sleep(rng.uniform(3.0, 7.0))
            smk_files = [
                {
                    "name": f"{sample}.vcf.gz",
                    "path": f"snakemake/calls/{sample}.vcf.gz",
                    "size_bytes": rng.randint(500_000, 5_000_000),
                    "mime_type": "application/gzip",
                    "description": "[Snakemake] Variant calls (VCF)",
                },
                {
                    "name": f"{sample}.annotated.vcf.gz",
                    "path": f"snakemake/annotate/{sample}.annotated.vcf.gz",
                    "size_bytes": rng.randint(1_000_000, 10_000_000),
                    "mime_type": "application/gzip",
                    "description": "[Snakemake] Annotated variants",
                },
                {
                    "name": "snakemake_report.html",
                    "path": "snakemake/report/snakemake_report.html",
                    "size_bytes": rng.randint(1_000_000, 4_000_000),
                    "mime_type": "text/html",
                    "description": "[Snakemake] Workflow execution report",
                },
                {
                    "name": f"{sample}.snakemake.log",
                    "path": f"snakemake/logs/{sample}.log",
                    "size_bytes": rng.randint(5_000, 50_000),
                    "mime_type": "text/plain",
                    "description": "[Snakemake] Execution log",
                },
            ]

            result = {
                "type": "files",
                "files": nf_files + smk_files,
                "instance_type": "c5.2xlarge",
                "runtime_seconds": int(time.time() - mixed_start),
            }
            logger.info("[pipeline] %s mixed pipeline done (%d files)", job_id, len(result["files"]))

        elif pipeline_id == "assessment":
            # ── Mutation Assessment path ──────────────────────────────────
            _update_job(job_id, instance_id=instance_id, stage="pipeline_running")
            append_log(job_id, "Stage: pipeline_running — running mutation assessment")
            logger.info("[pipeline] %s → assessment_running", job_id)

            wf_config = job.get("workflow_config") or {}
            if isinstance(wf_config, str):
                import json as _json
                wf_config = _json.loads(wf_config)

            source_job_id = wf_config.get("source_job_id") if isinstance(wf_config, dict) else None
            if not source_job_id:
                raise ValueError("assessment job requires workflow_config.source_job_id")

            source_job = _get_job(source_job_id)
            source_result = source_job.get("result") or {}
            if isinstance(source_result, str):
                import json as _json
                source_result = _json.loads(source_result)
            variants = source_result.get("variants", [])
            if not variants:
                raise ValueError(
                    f"Source job {source_job_id} has no variants in its result. "
                    "Run a sarek job first, then connect it to the Assessment node."
                )

            append_log(job_id, f"Loaded {len(variants)} variant(s) from source job {source_job_id}")
            assess_start = time.time()

            runner = get_assessment_runner()
            assessment_result = runner.run(job_id, variants, wf_config)

            report_path = assessment_result.report_path
            files = []
            if report_path:
                import os as _os
                files.append({
                    "name":        "mutation_assessment_report.pdf",
                    "path":        report_path,
                    "size_bytes":  _os.path.getsize(report_path) if _os.path.exists(report_path) else None,
                    "mime_type":   "application/pdf",
                    "description": "Mutation Assessment PDF Report",
                })

            result = {
                "type":            "assessment",
                "summary":         assessment_result.summary,
                "variants":        assessment_result.variants,
                "files":           files,
                "instance_type":   "local",
                "runtime_seconds": int(time.time() - assess_start),
                "report_sha256":   assessment_result.report_sha256,
            }
            append_log(job_id, f"Assessment complete — {len(assessment_result.variants)} variants annotated")
            logger.info("[pipeline] %s assessment done", job_id)

        elif pipeline_id == "bioscript":
            # ── BioScript (bash) path ─────────────────────────────────────
            _update_job(job_id, instance_id=instance_id, stage="pipeline_running")
            append_log(job_id, "Stage: pipeline_running — executing BioScript pipeline")
            logger.info("[pipeline] %s → bioscript_running", job_id)

            bsc = get_bioscript_runner()
            result = bsc.run(
                job["storage_key"],
                job["file_type"],
                job_id=job_id,
                workflow_config=job.get("workflow_config"),
            )
            append_log(job_id, f"BioScript completed — {len(result.get('files', []))} output file(s)")
            logger.info("[pipeline] %s BioScript done", job_id)

        elif pipeline_id == "snakemake":
            # ── Snakemake path ────────────────────────────────────────────
            _update_job(job_id, instance_id=instance_id, stage="snakemake_running")
            append_log(job_id, "Stage: snakemake_running — executing Snakemake workflow")
            logger.info("[pipeline] %s → snakemake_running", job_id)

            smk = get_snakemake_runner()
            result = smk.run(
                pipeline_id,
                job["storage_key"],
                job["file_type"],
                job_id=job_id,
                workflow_config=job.get("workflow_config"),
            )
            append_log(job_id, f"Snakemake completed — {len(result.get('files', []))} output file(s)")
            logger.info("[pipeline] %s Snakemake done", job_id)

        elif pipeline_id:
            # ── nf-core / Nextflow path ───────────────────────────────────
            _update_job(job_id, instance_id=instance_id, stage="pipeline_running")
            append_log(job_id, f"Stage: pipeline_running — running nf-core/{pipeline_id}")
            logger.info("[pipeline] %s → pipeline_running (%s)", job_id, pipeline_id)

            nf = get_nextflow_runner()
            result = nf.run(
                pipeline_id,
                job["storage_key"],
                job["file_type"],
                job_id=job_id,
                storage_key_r2=job.get("storage_key_r2"),
                workflow_config=job.get("workflow_config"),
            )
            append_log(job_id, f"Pipeline {pipeline_id} completed — {len(result.get('files', result.get('rows', [])))} result(s)")
            logger.info("[pipeline] %s pipeline done (%s)", job_id, pipeline_id)

        else:
            raise ValueError(f"Unknown pipeline_id: {pipeline_id!r}")

        # Stage 3: done
        from psycopg2.extras import Json
        append_log(job_id, "Job completed successfully")
        _update_job(
            job_id,
            status="completed",
            stage="done",
            result=Json(result),
        )
        logger.info("[pipeline] %s → completed", job_id)

        if instance_id:
            ec2.terminate_instance(instance_id)
            logger.info("[pipeline] %s instance terminated", job_id)

        # Send completion email (best-effort, non-blocking)
        try:
            user_email = _get_user_email(job.get("user_id", ""))
            if user_email:
                runtime_s = result.get("runtime_seconds", "?")
                send_job_notification(
                    to=user_email,
                    job_id=job_id,
                    status="completed",
                    pipeline=pipeline_id or "unknown",
                    job_name=job.get("job_name", ""),
                    runtime=f"{runtime_s}s",
                )
        except Exception as _email_exc:
            logger.warning("[pipeline] email notification failed: %s", _email_exc)

        return result

    except Exception as exc:
        logger.exception("[pipeline] %s failed: %s", job_id, exc)
        try:
            append_log(job_id, f"ERROR: {exc}")
        except Exception:
            pass
        try:
            _update_job(job_id, status="failed", error=str(exc)[:2000])
        except Exception as db_exc:
            # If we cannot write the failed status the frontend will poll forever.
            # Log at CRITICAL so ops can intervene, then still re-raise the
            # original exception below.
            logger.critical(
                "[pipeline] CRITICAL: could not write failed status to DB "
                "for job %s: %s",
                job_id,
                db_exc,
            )

        # Send failure email (best-effort)
        try:
            fresh_job = _get_job(job_id)
            user_email = _get_user_email(fresh_job.get("user_id", ""))
            if user_email:
                send_job_notification(
                    to=user_email,
                    job_id=job_id,
                    status="failed",
                    pipeline=fresh_job.get("pipeline_id") or "unknown",
                    job_name=fresh_job.get("job_name", ""),
                    error=str(exc),
                )
        except Exception as _email_exc:
            logger.warning("[pipeline] failure email notification failed: %s", _email_exc)

        raise


