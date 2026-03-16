from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    tier: Mapped[str] = mapped_column(String, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pipeline_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    stripe_session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    storage_key_r2: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    workflow_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    job_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sample_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
