"""Celery application configuration."""

import os

from celery import Celery
from celery.schedules import crontab
from garakboard.config import settings

celery_app = Celery(
    "garakboard",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["garakboard.worker.tasks"],
)

# Enable eager mode for testing (no broker needed)
if os.environ.get("TESTING") == "1" or os.environ.get("CELERY_TASK_ALWAYS_EAGER") == "1":
    celery_app.conf.task_always_eager = True

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Retry config for 429 rate limits
    task_default_retry_delay=30,
    task_max_retries=5,
    # Weekly scheduled scan discovery — Monday 02:00 UTC
    beat_schedule={
        "weekly-scan-discovery": {
            "task": "garakboard.worker.tasks.discover_and_schedule_scans",
            "schedule": crontab(hour=2, minute=0, day_of_week=1),
        }
    },
)