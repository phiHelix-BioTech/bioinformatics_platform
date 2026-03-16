from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "bioplatform",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.pipeline",
        "app.tasks.scrape_nfcore",
        "app.tasks.scrape_snakemake",
        "app.tasks.cleanup",
        "app.tasks.retention",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        "daily-data-retention": {
            "task": "app.tasks.retention.run_retention",
            "schedule": crontab(hour=2, minute=0),  # 02:00 UTC every day
        },
    },
)
