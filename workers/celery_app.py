from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "ai_testing_workers",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "workers.tasks.run_scan",
        "workers.tasks.generate_ai_cases"
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_soft_time_limit=1860,
    task_time_limit=1900,  # Leave a cleanup margin above the maximum 30-minute profile timeout.
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50
)
