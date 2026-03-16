from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Sample(Base):
    __tablename__ = "samples"

    id:              Mapped[str]           = mapped_column(String, primary_key=True)
    patient_id:      Mapped[str]           = mapped_column(String, nullable=False, index=True)
    user_id:         Mapped[str]           = mapped_column(String, nullable=False, index=True)
    sample_type:     Mapped[str]           = mapped_column(String, nullable=False)  # blood / saliva / tissue / etc.
    collection_date: Mapped[Optional[date]]= mapped_column(Date, nullable=True)
    description:     Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at:      Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_samples_patient_id", "patient_id"),
        Index("ix_samples_user_id", "user_id"),
    )
