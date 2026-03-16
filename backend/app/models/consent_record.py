"""KVKK / GDPR consent record model.

Every explicit consent action (or withdrawal) by a user is recorded here.
Rows are append-only — never updated or deleted (the record itself is evidence).
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Consent types
CONSENT_DATA_PROCESSING   = "data_processing"      # primary KVKK consent
CONSENT_MARKETING         = "marketing"             # optional
CONSENT_RESEARCH_SHARING  = "research_sharing"     # sharing de-identified data
CONSENT_ERASURE_REQUEST   = "erasure_request"      # KVKK right to erasure request


class ConsentRecord(Base):
    """Immutable audit trail of user consent actions."""
    __tablename__ = "consent_records"

    id:            Mapped[str]      = mapped_column(String, primary_key=True)
    user_id:       Mapped[str]      = mapped_column(String, nullable=False, index=True)
    consent_type:  Mapped[str]      = mapped_column(String, nullable=False)
    consented:     Mapped[bool]     = mapped_column(Boolean, nullable=False)
    ip_address:    Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent:    Mapped[str | None] = mapped_column(String, nullable=True)
    consented_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
