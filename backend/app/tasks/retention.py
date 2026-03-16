"""Data retention Celery task.

Runs daily via Celery beat.  Enforces configurable retention windows:

  RAW_FILE_RETENTION_DAYS  (default 30)
    Raw upload objects (storage_key / storage_key_r2) are deleted from
    object storage after this many days.  The job row is kept so the user
    can still see their history, but the file can no longer be re-processed.

  REPORT_RETENTION_DAYS  (default 1825 = 5 years)
    Job result JSON (which contains the assessment PDF path / variant data)
    is nulled out after this many days.  This satisfies KVKK's requirement
    to not hold personal health data longer than necessary.

Enable by setting RETENTION_ENABLED=true in the environment.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import select

logger = logging.getLogger(__name__)


@shared_task(name="app.tasks.retention.run_retention")
def run_retention() -> dict:
    """Synchronous Celery task (uses psycopg2 sync engine via the worker)."""
    from app.config import settings

    if not settings.RETENTION_ENABLED:
        logger.debug("[retention] disabled — skipping")
        return {"skipped": True}

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    # Use the sync DATABASE_URL (worker uses psycopg2)
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url, pool_pre_ping=True)

    from app.models.job import Job
    from app.services.storage.base import get_storage_backend

    storage = get_storage_backend()
    now = datetime.now(timezone.utc)

    raw_cutoff    = now - timedelta(days=settings.RAW_FILE_RETENTION_DAYS)
    report_cutoff = now - timedelta(days=settings.REPORT_RETENTION_DAYS)

    raw_purged = 0
    report_purged = 0

    with Session(engine) as db:
        # ── Phase 1: delete raw files past RAW_FILE_RETENTION_DAYS ──────────
        stmt = select(Job).where(
            Job.created_at < raw_cutoff,
            Job.storage_key.isnot(None),
        )
        for job in db.scalars(stmt):
            for key in (job.storage_key, job.storage_key_r2):
                if key:
                    try:
                        storage.delete(key)
                    except Exception as exc:
                        logger.warning("[retention] delete %s failed: %s", key, exc)
            job.storage_key = None
            job.storage_key_r2 = None
            raw_purged += 1

        # ── Phase 2: null out results past REPORT_RETENTION_DAYS ────────────
        stmt2 = select(Job).where(
            Job.created_at < report_cutoff,
            Job.result.isnot(None),
        )
        for job in db.scalars(stmt2):
            job.result = None
            report_purged += 1

        db.commit()

    logger.info(
        "[retention] raw_purged=%d report_purged=%d",
        raw_purged, report_purged,
    )
    return {"raw_purged": raw_purged, "report_purged": report_purged}
