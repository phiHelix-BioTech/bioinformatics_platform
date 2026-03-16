import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.schemas.upload import PresignRequest, PresignResponse
from app.services.cost_estimator import estimate
from app.services.storage.base import get_storage_backend
from app.services.vcf_validator import VCFValidationError, validate_vcf_bytes

router = APIRouter()

def _is_vcf_filename(filename: str) -> bool:
    lower = filename.lower()
    return lower.endswith(".vcf") or lower.endswith(".vcf.gz") or lower.endswith(".bcf")


@router.post("/presign", response_model=PresignResponse)
async def presign_upload(body: PresignRequest):
    if body.file_size_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_gb = settings.MAX_UPLOAD_SIZE_BYTES / (1024 ** 3)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {limit_gb:.0f} GB.",
        )

    ext = os.path.splitext(body.filename)[1].lower() or ".fastq"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    storage_key = f"uploads/{unique_name}"

    storage = get_storage_backend()
    upload_url = storage.generate_upload_url(storage_key)

    tier_est = estimate(body.file_size_bytes, pipeline_id=None, n_samples=1)

    return PresignResponse(
        upload_url=upload_url,
        storage_key=storage_key,
        recommended_tier=tier_est.tier,
        estimated_cost_usd=tier_est.cost_usd,
        tier_rationale=tier_est.rationale,
    )


@router.get("/estimate")
async def get_estimate(
    pipeline_id: Optional[str] = Query(None),
    file_size_bytes: int = Query(0, ge=0),
    n_samples: int = Query(1, ge=1, le=10000),
):
    """Return a pipeline-aware cost estimate without creating an upload URL."""
    est = estimate(file_size_bytes, pipeline_id=pipeline_id, n_samples=n_samples)
    return {
        "tier":                 est.tier,
        "instance_type":        est.instance_type,
        "estimated_cost_usd":   est.cost_usd,
        "rationale":            est.rationale,
        "estimated_hours":      est.estimated_hours,
        "pipeline_description": est.pipeline_description,
    }


@router.get("/local/{filename}")
async def serve_local_file(filename: str):
    """Serve a file from the local uploads directory (e.g. assessment PDF reports)."""
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(settings.UPLOADS_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@router.put("/local/{filename}")
async def upload_local(filename: str, request: Request):
    """Mock S3 endpoint: receives raw file bytes and saves to the uploads volume."""
    safe_filename = os.path.basename(filename)
    dest_path = os.path.join(settings.UPLOADS_DIR, safe_filename)

    os.makedirs(settings.UPLOADS_DIR, exist_ok=True)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body.")

    if len(body) > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_gb = settings.MAX_UPLOAD_SIZE_BYTES / (1024 ** 3)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {limit_gb:.0f} GB.",
        )

    # Validate VCF header if this looks like a VCF file
    if _is_vcf_filename(safe_filename):
        try:
            validate_vcf_bytes(body)
        except VCFValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid VCF file: {exc}")

    with open(dest_path, "wb") as f:
        f.write(body)

    return Response(status_code=200)
