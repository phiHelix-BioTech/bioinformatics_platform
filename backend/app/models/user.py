from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Valid roles: "user" (default), "clinician", "admin"
ROLES = {"user", "clinician", "admin"}


class User(Base):
    __tablename__ = "users"

    id:              Mapped[str]        = mapped_column(String, primary_key=True)
    email:           Mapped[str]        = mapped_column(String, unique=True, nullable=False, index=True)
    hashed_password: Mapped[str]        = mapped_column(String, nullable=False)
    is_active:       Mapped[bool]       = mapped_column(Boolean, default=True, nullable=False)
    role:            Mapped[str]        = mapped_column(String, nullable=False, default="user", server_default="user")
    # MFA (TOTP via pyotp)
    mfa_secret:      Mapped[str | None] = mapped_column(String, nullable=True)
    mfa_enabled:     Mapped[bool]       = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # KVKK / data residency
    data_residency:  Mapped[str]        = mapped_column(String, nullable=False, default="TR", server_default="TR")
    # Email verification
    email_verified:              Mapped[bool]             = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    email_verification_token:    Mapped[Optional[str]]    = mapped_column(String, nullable=True)
    # Password reset
    password_reset_token:        Mapped[Optional[str]]    = mapped_column(String, nullable=True)
    password_reset_expires:      Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Account lockout
    failed_login_attempts:       Mapped[int]              = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until:                Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at:      Mapped[datetime]   = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
