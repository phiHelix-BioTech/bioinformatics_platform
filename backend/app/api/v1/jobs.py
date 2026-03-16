import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.api.v1.deps import get_current_user
from app.database import get_db
from app.limiter import limiter
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobCreate, JobResponse, JobListResponse
from app.services.audit import log_audit, _ip, _ua
from app.tasks.pipeline import run_pipeline

_CANCELLABLE_STATUSES = {"pending", "running"}

router = APIRouter()


def _serialize_job(job: Job) -> JobResponse:
    result = job.result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            result = None

    return JobResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        tier=job.tier,
        estimated_cost_usd=job.estimated_cost_usd,
        pipeline_id=job.pipeline_id,
        storage_key_r2=job.storage_key_r2,
        workflow_config=job.workflow_config,
        job_name=job.job_name,
        created_at=job.created_at,
        result=result,
        error=job.error,
    )


def _serialize_job_list(job: Job) -> JobListResponse:
    return JobListResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        tier=job.tier,
        estimated_cost_usd=job.estimated_cost_usd,
        pipeline_id=job.pipeline_id,
        job_name=job.job_name,
        created_at=job.created_at,
    )


@router.get("", response_model=list[JobListResponse])
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Job)
        .where(Job.user_id == current_user.id)
        .order_by(desc(Job.created_at))
        .limit(50)
    )
    return [_serialize_job_list(job) for job in result.scalars().all()]


@router.post("", response_model=JobResponse, status_code=201)
@limiter.limit("20/minute")
async def create_job(
    request: Request,
    body: JobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = Job(
        id=str(uuid.uuid4()),
        status="pending",
        stage=None,
        file_type=body.file_type.lower(),
        storage_key=body.storage_key,
        tier=body.tier,
        estimated_cost_usd=body.estimated_cost_usd,
        pipeline_id=body.pipeline_id or None,
        storage_key_r2=body.storage_key_r2 or None,
        workflow_config=body.workflow_config or None,
        job_name=body.job_name or None,
        user_id=current_user.id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    task = run_pipeline.delay(job.id)
    job.celery_task_id = task.id
    await db.commit()

    await log_audit("job.create", user_id=current_user.id, resource_type="job", resource_id=job.id,
                    ip_address=_ip(request), user_agent=_ua(request),
                    meta={"pipeline_id": job.pipeline_id, "tier": job.tier})
    return _serialize_job(job)


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _serialize_job(job)


@router.get("/{job_id}/download")
async def get_download_url(
    job_id: str,
    path: str = Query(..., description="S3 URI or object key of the file to download"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a short-lived presigned download URL for a result file.

    Security: verifies the user owns the job and the requested path
    appears verbatim in the job's result files list.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")

    # Verify the requested path is actually part of this job's result
    job_result = job.result
    if isinstance(job_result, str):
        try:
            job_result = json.loads(job_result)
        except Exception:
            job_result = None

    if job_result:
        files = job_result.get("files", [])
        allowed_paths = {f.get("path", "") for f in files}
        if path not in allowed_paths:
            raise HTTPException(status_code=403, detail="File not part of this job's results.")

    from app.services.storage.base import get_storage_backend
    storage = get_storage_backend()
    url = storage.generate_download_url(path)
    if url is None:
        raise HTTPException(status_code=501, detail="Download URLs not supported by this storage backend.")
    return {"url": url}


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    offset: int = Query(0, ge=0, description="Return lines from this index onward"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return buffered log lines for a job since ``offset``.

    Poll this endpoint every 1–2 s while the job is running to get a
    live terminal-style view.  Stop polling when ``done`` is true.

    Response shape::

        {
            "lines":       ["[12:01:03] Job started ...", ...],
            "next_offset": 5,
            "done":        false
        }
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")

    from app.services.log_streamer import get_logs
    lines = get_logs(job_id, offset)
    done = job.status in ("completed", "failed", "cancelled")

    return {
        "lines": lines,
        "next_offset": offset + len(lines),
        "done": done,
    }


@router.get("/{job_id}/vcf")
async def get_vcf_page(
    job_id: str,
    offset: int = Query(0, ge=0, description="Variant index to start from"),
    limit: int = Query(100, ge=1, le=500, description="Max variants to return"),
    chrom: str = Query("", description="Filter by chromosome (empty = all)"),
    filter_pass: bool = Query(False, description="Return only PASS variants"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a page of VCF variants from a completed job's result.

    Supports offset-based pagination, chromosome filter, and PASS-only filter.
    Designed for large VCFs that cannot be embedded entirely in the job JSON.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")

    job_result = job.result
    if isinstance(job_result, str):
        try:
            job_result = json.loads(job_result)
        except Exception:
            job_result = None

    if not job_result or job_result.get("type") != "vcf":
        raise HTTPException(status_code=422, detail="Job result is not VCF type.")

    variants = job_result.get("variants", [])

    # Apply filters
    if chrom:
        variants = [v for v in variants if v.get("chrom", "") == chrom]
    if filter_pass:
        variants = [v for v in variants if v.get("filter", "") == "PASS"]

    total = len(variants)
    page  = variants[offset : offset + limit]

    return {
        "variants":    page,
        "total":       total,
        "offset":      offset,
        "limit":       limit,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "chroms":      sorted({v.get("chrom", "") for v in job_result.get("variants", [])} - {""}),
    }


@router.post("/{job_id}/retry", response_model=JobResponse, status_code=201)
async def retry_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new job with the same inputs as an existing failed/cancelled job."""
    result = await db.execute(select(Job).where(Job.id == job_id))
    original = result.scalar_one_or_none()
    if original is None or original.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")
    if original.status not in ("failed", "cancelled"):
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried.")

    new_job = Job(
        id=str(uuid.uuid4()),
        status="pending",
        stage=None,
        file_type=original.file_type,
        storage_key=original.storage_key,
        storage_key_r2=original.storage_key_r2,
        tier=original.tier,
        estimated_cost_usd=original.estimated_cost_usd,
        pipeline_id=original.pipeline_id,
        workflow_config=original.workflow_config,
        job_name=original.job_name,
        user_id=current_user.id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(new_job)
    await db.commit()
    await db.refresh(new_job)

    task = run_pipeline.delay(new_job.id)
    new_job.celery_task_id = task.id
    await db.commit()

    await log_audit("job.retry", user_id=current_user.id, resource_type="job", resource_id=new_job.id,
                    meta={"original_job_id": job_id, "pipeline_id": new_job.pipeline_id})
    return _serialize_job(new_job)


@router.delete("/{job_id}", status_code=204)
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled in its current state.")

    # Revoke the Celery task (best-effort — sends SIGTERM to worker if running)
    if job.celery_task_id:
        try:
            from celery.result import AsyncResult
            from app.celery_app import celery_app
            AsyncResult(job.celery_task_id, app=celery_app).revoke(terminate=True, signal="SIGTERM")
        except Exception:
            pass  # non-fatal; DB update below still marks it cancelled

    # Terminate the underlying AWS Batch job if one exists (BioScript / Custom runners)
    try:
        from app.services.batch_tracker import get_batch_job_id, delete_batch_job_id
        from app.config import settings as _settings
        import boto3 as _boto3
        batch_job_id = get_batch_job_id(job.id)
        if batch_job_id:
            _boto3.client("batch", region_name=_settings.AWS_REGION).terminate_job(
                jobId=batch_job_id,
                reason="Cancelled by user via API",
            )
            delete_batch_job_id(job.id)
    except Exception:
        pass  # non-fatal

    job.status = "cancelled"
    job.stage = None
    job.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await log_audit("job.cancel", user_id=current_user.id, resource_type="job", resource_id=job_id,
                    meta={"pipeline_id": job.pipeline_id})


@router.get("/{job_id}/sv")
async def get_sv_variants(
    job_id: str,
    svtype: str = Query("", description="Filter by SV type (DEL, DUP, INV, INS, BND, CNV — empty = all)"),
    chrom: str = Query("", description="Filter by chromosome (empty = all)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Parse a VCF result file and return structural variant / CNV records.

    The job result must have a file with path ending in ``.vcf`` or ``.vcf.gz``
    accessible via the local storage backend, or be a completed VCF-type job.
    Supports DEL, DUP, INV, INS, BND, CNV, TRA record types.
    """
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found.")

    job_result = job.result
    if isinstance(job_result, str):
        try:
            job_result = json.loads(job_result)
        except Exception:
            job_result = None

    if not job_result:
        raise HTTPException(status_code=422, detail="Job has no result data yet.")

    # Find a VCF file path in the result
    vcf_path: str | None = None
    for f in job_result.get("files", []):
        p = f.get("path", "")
        if p.endswith(".vcf") or p.endswith(".vcf.gz"):
            # Strip s3:// prefix for local backend; local paths start with /
            if p.startswith("/"):
                vcf_path = p
            break

    if not vcf_path:
        raise HTTPException(status_code=422, detail="No VCF file found in job results.")

    from app.services.sv_parser import parse_sv_vcf
    all_records = parse_sv_vcf(vcf_path)

    if svtype:
        all_records = [r for r in all_records if r["svtype"] == svtype.upper()]
    if chrom:
        all_records = [r for r in all_records if r["chrom"] == chrom]

    total = len(all_records)
    page = all_records[offset: offset + limit]

    return {
        "variants": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(page) if offset + len(page) < total else None,
        "sv_types": sorted({r["svtype"] for r in all_records}),
        "chroms": sorted({r["chrom"] for r in all_records}),
    }
