"""
iyzico payment endpoints (alternative to Stripe for Turkish market).

Flow:
  1. POST /payments/iyzico/checkout  → initialize checkout form → return HTML content + token
  2. Frontend renders the iyzico checkout form (iframe or redirect)
  3. iyzico POSTs callback to POST /payments/iyzico/callback with token + status
  4. Callback handler retrieves result, creates Job + dispatches Celery task
  5. Browser GET /payments/iyzico/session/{token} → poll until job_id appears

Requires: pip install iyzipay
Set IYZICO_API_KEY, IYZICO_SECRET_KEY, IYZICO_BASE_URL in environment.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.v1.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.job import Job
from app.models.user import User
from app.services.tckn import validate_tckn
from app.tasks.pipeline import run_pipeline

router = APIRouter()
logger = logging.getLogger(__name__)

_redis_client: Optional[redis_lib.Redis] = None


def _redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(
            settings.CELERY_BROKER_URL, decode_responses=True
        )
    return _redis_client


def _iyzico_options() -> dict:
    if not settings.IYZICO_API_KEY or not settings.IYZICO_SECRET_KEY:
        raise HTTPException(status_code=503, detail="iyzico payment is not configured.")
    return {
        "api_key":    settings.IYZICO_API_KEY,
        "secret_key": settings.IYZICO_SECRET_KEY,
        "base_url":   settings.IYZICO_BASE_URL,
    }


class IyzicoCheckoutRequest(BaseModel):
    storage_key: str
    file_type: str
    tier: str
    pipeline_id: Optional[str] = None
    estimated_cost_usd: float
    n_samples: int = 1
    storage_key_r2: Optional[str] = None
    workflow_config: Optional[Any] = None
    job_name: Optional[str] = None
    # Buyer info required by iyzico (collected from user profile or passed from frontend)
    buyer_name: str = "User"
    buyer_surname: str = "User"
    buyer_phone: str = "+905000000000"
    buyer_city: str = "Istanbul"
    buyer_country: str = "Turkey"
    buyer_zip: str = "34000"
    buyer_address: str = "Istanbul"
    buyer_identity_number: str = "00000000000"  # TC Kimlik No — validated before submission


class IyzicoCheckoutResponse(BaseModel):
    checkout_form_content: str
    token: str


@router.post("/checkout", response_model=IyzicoCheckoutResponse)
async def iyzico_checkout(
    body: IyzicoCheckoutRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Create an iyzico Checkout Form session. Returns embedded HTML form content."""
    import asyncio

    # Validate TC Kimlik No if provided (skip the default placeholder)
    tckn = body.buyer_identity_number.strip()
    if tckn and tckn != "00000000000":
        if not validate_tckn(tckn):
            raise HTTPException(status_code=400, detail="Geçersiz TC Kimlik No.")

    options = _iyzico_options()

    # TRY amount — convert USD at approximate rate
    # In production, fetch live rate from TCMB or pass TRY amount from frontend
    try_amount = round(body.estimated_cost_usd * float(settings.IYZICO_USD_TO_TRY_RATE), 2)
    if try_amount < 1.0:
        try_amount = 1.0
    amount_str = f"{try_amount:.2f}"

    conversation_id = str(uuid.uuid4())
    basket_id = str(uuid.uuid4())

    # Store workflow_config in Redis (same pattern as Stripe)
    workflow_config_ref = ""
    if body.workflow_config:
        workflow_config_ref = str(uuid.uuid4())
        _redis().setex(
            f"wfcfg:{workflow_config_ref}",
            86400,
            json.dumps(body.workflow_config),
        )

    # Store job metadata keyed by conversation_id for callback retrieval
    _redis().setex(
        f"iyzico_meta:{conversation_id}",
        86400,
        json.dumps({
            "storage_key":         body.storage_key,
            "file_type":           body.file_type,
            "tier":                body.tier,
            "pipeline_id":         body.pipeline_id or "",
            "user_id":             current_user.id,
            "estimated_cost_usd":  body.estimated_cost_usd,
            "storage_key_r2":      body.storage_key_r2 or "",
            "workflow_config_ref": workflow_config_ref,
            "job_name":            (body.job_name or "")[:200],
        }),
    )

    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "1.1.1.1")
    )

    iyzico_request = {
        "locale":             "tr",
        "conversationId":     conversation_id,
        "price":              amount_str,
        "paidPrice":          amount_str,
        "currency":           "TRY",
        "basketId":           basket_id,
        "paymentGroup":       "PRODUCT",
        "callbackUrl":        f"{settings.APP_BASE_URL}/api/v1/payments/iyzico/callback",
        "enabledInstallments": [1, 2, 3, 6, 9, 12],
        "buyer": {
            "id":                  current_user.id,
            "name":                body.buyer_name,
            "surname":             body.buyer_surname,
            "gsmNumber":           body.buyer_phone,
            "email":               current_user.email,
            "identityNumber":      body.buyer_identity_number,
            "lastLoginDate":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "registrationDate":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "registrationAddress": body.buyer_address,
            "ip":                  client_ip,
            "city":                body.buyer_city,
            "country":             body.buyer_country,
            "zipCode":             body.buyer_zip,
        },
        "shippingAddress": {
            "contactName": f"{body.buyer_name} {body.buyer_surname}",
            "city":        body.buyer_city,
            "country":     body.buyer_country,
            "address":     body.buyer_address,
            "zipCode":     body.buyer_zip,
        },
        "billingAddress": {
            "contactName": f"{body.buyer_name} {body.buyer_surname}",
            "city":        body.buyer_city,
            "country":     body.buyer_country,
            "address":     body.buyer_address,
            "zipCode":     body.buyer_zip,
        },
        "basketItems": [
            {
                "id":        basket_id,
                "name":      f"Pipeline: {body.pipeline_id or 'bioinformatics'}",
                "category1": "Bioinformatics",
                "itemType":  "VIRTUAL",
                "price":     amount_str,
            }
        ],
    }

    def _create_form():
        import iyzipay
        result = iyzipay.CheckoutFormInitialize().create(iyzico_request, options)
        return json.loads(result.read().decode("utf-8"))

    try:
        data = await asyncio.to_thread(_create_form)
    except Exception as exc:
        logger.error("[iyzico] checkout form creation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"iyzico error: {exc}")

    if data.get("status") != "success":
        raise HTTPException(
            status_code=400,
            detail=f"iyzico checkout failed: {data.get('errorMessage', 'unknown error')}",
        )

    return IyzicoCheckoutResponse(
        checkout_form_content=data.get("checkoutFormContent", ""),
        token=data.get("token", conversation_id),
    )


@router.post("/callback")
async def iyzico_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    iyzico posts here after payment attempt (success or failure).
    NOT JWT-protected — iyzico signature is verified via token retrieval.
    """
    import asyncio
    form_data = await request.form()
    token = form_data.get("token", "")
    status = form_data.get("status", "")

    if not token:
        raise HTTPException(status_code=400, detail="Missing token.")

    options = _iyzico_options()

    def _retrieve():
        import iyzipay
        result = iyzipay.CheckoutForm().retrieve(
            {"locale": "tr", "conversationId": token, "token": token},
            options,
        )
        return json.loads(result.read().decode("utf-8"))

    try:
        data = await asyncio.to_thread(_retrieve)
    except Exception as exc:
        logger.error("[iyzico] payment retrieval failed for token %s: %s", token, exc)
        raise HTTPException(status_code=502, detail="Payment verification failed.")

    if data.get("paymentStatus") != "SUCCESS":
        logger.info("[iyzico] payment not successful for token %s: %s", token, data.get("paymentStatus"))
        return {"ok": False, "status": data.get("paymentStatus")}

    conversation_id = data.get("conversationId", token)
    raw_meta = _redis().get(f"iyzico_meta:{conversation_id}")
    if not raw_meta:
        logger.warning("[iyzico] no metadata found for conversation_id=%s", conversation_id)
        return {"ok": True, "note": "payment recorded but job metadata expired"}

    meta = json.loads(raw_meta)
    _redis().delete(f"iyzico_meta:{conversation_id}")

    workflow_config = None
    wfcfg_ref = meta.get("workflow_config_ref", "")
    if wfcfg_ref:
        raw = _redis().get(f"wfcfg:{wfcfg_ref}")
        if raw:
            workflow_config = json.loads(raw)
            _redis().delete(f"wfcfg:{wfcfg_ref}")

    job = Job(
        id=str(uuid.uuid4()),
        status="pending",
        stage=None,
        file_type=meta.get("file_type", "fastq"),
        storage_key=meta["storage_key"],
        tier=meta.get("tier", "small"),
        estimated_cost_usd=float(meta.get("estimated_cost_usd", 0)),
        pipeline_id=meta.get("pipeline_id") or None,
        storage_key_r2=meta.get("storage_key_r2") or None,
        workflow_config=workflow_config,
        job_name=meta.get("job_name") or None,
        user_id=meta.get("user_id"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    task = run_pipeline.delay(job.id)
    job.celery_task_id = task.id
    await db.commit()

    # Store job_id keyed by token for polling
    _redis().setex(f"iyzico_job:{token}", 3600, job.id)
    logger.info("[iyzico] job %s created for token %s", job.id, token)
    return {"ok": True, "job_id": job.id}


@router.get("/session/{token}")
async def iyzico_session_poll(
    token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll for job_id after iyzico redirect. Returns {job_id: null} while waiting."""
    job_id = _redis().get(f"iyzico_job:{token}")
    if not job_id:
        return {"job_id": None}
    result = await db.execute(select(Job).where(Job.id == job_id, Job.user_id == current_user.id))
    job = result.scalar_one_or_none()
    return {"job_id": job.id if job else None}
