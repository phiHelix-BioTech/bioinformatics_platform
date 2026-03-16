import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.v1.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.limiter import limiter
from app.models.consent_record import ConsentRecord
from app.models.user import User
from app.services.audit import log_audit, _ip, _ua
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    hash_password,
    revoke_refresh_token,
    verify_password,
    verify_refresh_token,
)

router = APIRouter()


# ── Schemas (small, auth-only — no separate file needed) ──────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime
    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    mfa_required: bool = False
    mfa_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class MfaSetupOut(BaseModel):
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: str


class MfaCompleteRequest(BaseModel):
    mfa_token: str
    code: str


class ConsentRequest(BaseModel):
    consent_type: str   # e.g. "kvkk", "marketing"
    consented: bool


class ConsentOut(BaseModel):
    consent_type: str
    consented: bool
    consented_at: datetime
    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=201)
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(User).where(User.email == body.email.lower().strip())
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered.")

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    verification_token = secrets.token_urlsafe(32)
    user = User(
        id=str(uuid.uuid4()),
        email=body.email.lower().strip(),
        hashed_password=hash_password(body.password),
        email_verified=False,
        email_verification_token=verification_token,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Send verification email (best-effort)
    try:
        from app.services.email import send_email
        verify_url = f"{settings.APP_BASE_URL}/verify-email?token={verification_token}"
        send_email(
            to=user.email,
            subject="Verify your Bioplatform account",
            body=(
                f"Please verify your email address by clicking the link below:\n\n"
                f"{verify_url}\n\n"
                f"This link expires in 24 hours."
            ),
        )
    except Exception:
        pass  # non-fatal

    await log_audit("auth.register", user_id=user.id, resource_type="user", resource_id=user.id,
                    ip_address=_ip(request), user_agent=_ua(request))
    return user


@router.post("/login", response_model=TokenOut)
@limiter.limit("10/minute")
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.email == form.username.lower().strip())
    )
    user = result.scalar_one_or_none()

    _bad_creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not user:
        await log_audit("auth.login_failed", ip_address=_ip(request), user_agent=_ua(request),
                        meta={"email": form.username.lower().strip()})
        raise _bad_creds_exc

    # Account lockout check
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Account locked due to too many failed login attempts. "
                f"Try again after {user.locked_until.strftime('%H:%M UTC')} "
                f"or reset your password."
            ),
        )

    if not verify_password(form.password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=settings.LOCKOUT_MINUTES)
            user.failed_login_attempts = 0
        await db.commit()
        await log_audit("auth.login_failed", ip_address=_ip(request), user_agent=_ua(request),
                        meta={"email": form.username.lower().strip(),
                              "attempts": user.failed_login_attempts})
        raise _bad_creds_exc

    # Successful login — reset lockout state
    user.failed_login_attempts = 0
    user.locked_until = None
    await db.commit()

    await log_audit("auth.login", user_id=user.id, resource_type="user", resource_id=user.id,
                    ip_address=_ip(request), user_agent=_ua(request))

    # If MFA is enabled, issue a short-lived MFA token instead of a full JWT
    if user.mfa_enabled:
        mfa_token = create_access_token(user.id, expires_minutes=5, purpose="mfa")
        return TokenOut(access_token="", mfa_required=True, mfa_token=mfa_token)

    refresh_token = create_refresh_token(user.id)
    return TokenOut(
        access_token=create_access_token(user.id),
        refresh_token=refresh_token,
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.delete("/me", status_code=204)
async def delete_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """GDPR right-to-erasure: delete the current user and all associated data.

    Deletes the user row (which cascades to jobs via application logic),
    then queues background deletion of all S3 objects owned by the user.
    """
    from sqlalchemy import delete as sa_delete
    from app.models.job import Job

    # Collect all storage keys before deletion so we can clean up S3
    jobs_result = await db.execute(
        select(Job.storage_key, Job.storage_key_r2)
        .where(Job.user_id == current_user.id)
    )
    storage_keys = []
    for row in jobs_result:
        if row.storage_key:
            storage_keys.append(row.storage_key)
        if row.storage_key_r2:
            storage_keys.append(row.storage_key_r2)

    # Delete jobs first (no FK cascade on user_id by design)
    await db.execute(sa_delete(Job).where(Job.user_id == current_user.id))
    # Delete user
    await db.delete(current_user)
    await db.commit()

    # Queue S3 cleanup task (best-effort, non-blocking)
    if storage_keys:
        try:
            from app.tasks.cleanup import delete_storage_keys
            delete_storage_keys.delay(storage_keys)
        except Exception:
            pass  # non-fatal — keys can be cleaned up manually


# ── MFA endpoints ──────────────────────────────────────────────────────────

@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a new TOTP secret and return the provisioning URI. Does not
    enable MFA — call POST /mfa/verify with a valid code to activate."""
    secret = pyotp.random_base32()
    current_user.mfa_secret = secret
    await db.commit()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=current_user.email, issuer_name=settings.MFA_ISSUER
    )
    return MfaSetupOut(secret=secret, provisioning_uri=uri)


@router.post("/mfa/verify", response_model=TokenOut)
async def mfa_verify(
    body: MfaVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Verify a TOTP code and enable MFA for the account. Returns a fresh JWT."""
    if not current_user.mfa_secret:
        raise HTTPException(400, "MFA setup not initiated. Call POST /mfa/setup first.")
    if not pyotp.TOTP(current_user.mfa_secret).verify(body.code, valid_window=1):
        raise HTTPException(400, "Invalid TOTP code.")
    current_user.mfa_enabled = True
    await db.commit()
    return TokenOut(access_token=create_access_token(current_user.id))


@router.delete("/mfa", status_code=204)
async def mfa_disable(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Disable MFA and clear the stored secret."""
    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    await db.commit()


@router.post("/mfa/complete", response_model=TokenOut)
async def mfa_complete(
    body: MfaCompleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a short-lived MFA token + valid TOTP code for a full JWT.

    Called by the frontend after the user enters their authenticator code
    at the MFA challenge screen.
    """
    from app.services.auth import decode_access_token

    payload = decode_access_token(body.mfa_token, purpose="mfa")
    if not payload:
        raise HTTPException(401, "Invalid or expired MFA token.")

    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user or not user.mfa_enabled or not user.mfa_secret:
        raise HTTPException(401, "MFA not configured for this account.")

    if not pyotp.TOTP(user.mfa_secret).verify(body.code, valid_window=1):
        raise HTTPException(400, "Invalid TOTP code.")

    return TokenOut(access_token=create_access_token(user.id))


# ── KVKK consent endpoints ────────────────────────────────────────────────

@router.post("/consent", response_model=ConsentOut, status_code=201)
async def record_consent(
    request: Request,
    body: ConsentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record (or update) a KVKK/GDPR consent choice for the current user."""
    # Upsert: if a record for this (user, type) already exists, update it
    existing = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.user_id == current_user.id,
            ConsentRecord.consent_type == body.consent_type,
        )
    )
    record = existing.scalar_one_or_none()
    if record:
        record.consented = body.consented
        record.ip_address = _ip(request)
        record.user_agent = _ua(request)
        record.consented_at = datetime.now(timezone.utc)
    else:
        record = ConsentRecord(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            consent_type=body.consent_type,
            consented=body.consented,
            ip_address=_ip(request),
            user_agent=_ua(request),
            consented_at=datetime.now(timezone.utc),
        )
        db.add(record)
    await db.commit()
    await db.refresh(record)
    await log_audit(
        "auth.consent",
        user_id=current_user.id,
        resource_type="consent_record",
        resource_id=record.id,
        ip_address=_ip(request),
        user_agent=_ua(request),
        meta={"consent_type": body.consent_type, "consented": body.consented},
    )
    return record


@router.get("/consent", response_model=list[ConsentOut])
async def get_consents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all consent records for the current user."""
    result = await db.execute(
        select(ConsentRecord).where(ConsentRecord.user_id == current_user.id)
    )
    return result.scalars().all()


# ── Email verification ─────────────────────────────────────────────────────

@router.get("/verify-email", status_code=200)
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Verify email address via token sent on registration."""
    result = await db.execute(
        select(User).where(User.email_verification_token == token)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(400, "Invalid or expired verification token.")
    user.email_verified = True
    user.email_verification_token = None
    await db.commit()
    return {"message": "Email verified successfully."}


# ── Password reset ─────────────────────────────────────────────────────────

@router.post("/forgot-password", status_code=202)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a password-reset email. Always returns 202 to prevent email enumeration."""
    result = await db.execute(
        select(User).where(User.email == body.email.lower().strip())
    )
    user = result.scalar_one_or_none()
    if user:
        reset_token = secrets.token_urlsafe(32)
        user.password_reset_token = reset_token
        user.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await db.commit()
        try:
            from app.services.email import send_email
            reset_url = f"{settings.APP_BASE_URL}/reset-password?token={reset_token}"
            send_email(
                to=user.email,
                subject="Reset your Bioplatform password",
                body=(
                    f"You requested a password reset. Click the link below:\n\n"
                    f"{reset_url}\n\n"
                    f"This link expires in 1 hour. If you did not request this, ignore this email."
                ),
            )
        except Exception:
            pass  # non-fatal
    return {"message": "If that email exists, a reset link has been sent."}


@router.post("/reset-password", status_code=200)
async def reset_password(
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set a new password using a reset token."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    result = await db.execute(
        select(User).where(User.password_reset_token == body.token)
    )
    user = result.scalar_one_or_none()
    if not user or not user.password_reset_expires:
        raise HTTPException(400, "Invalid or expired reset token.")
    if user.password_reset_expires < datetime.now(timezone.utc):
        raise HTTPException(400, "Reset token has expired. Please request a new one.")
    user.hashed_password = hash_password(body.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    user.failed_login_attempts = 0
    user.locked_until = None
    await db.commit()
    return {"message": "Password reset successfully."}


# ── Refresh token ──────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenOut)
async def refresh_access_token(body: RefreshRequest):
    """Exchange a valid refresh token for a new access token."""
    user_id = verify_refresh_token(body.refresh_token)
    if not user_id:
        raise HTTPException(401, "Invalid or expired refresh token.")
    # Rotate: revoke old token, issue new pair
    revoke_refresh_token(body.refresh_token)
    new_refresh = create_refresh_token(user_id)
    return TokenOut(
        access_token=create_access_token(user_id),
        refresh_token=new_refresh,
    )


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest):
    """Revoke the refresh token (client should also discard the access token)."""
    revoke_refresh_token(body.refresh_token)
