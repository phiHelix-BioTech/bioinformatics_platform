from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Patient(Base):
    __tablename__ = "patients"

    id:             Mapped[str]           = mapped_column(String, primary_key=True)
    user_id:        Mapped[str]           = mapped_column(String, nullable=False, index=True)
    name:           Mapped[str]           = mapped_column(String, nullable=False)
    date_of_birth:  Mapped[Optional[date]]= mapped_column(Date, nullable=True)
    sex:            Mapped[Optional[str]] = mapped_column(String, nullable=True)   # M / F / Other
    notes:          Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at:     Mapped[datetime]      = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_patients_user_id", "user_id"),
    )
