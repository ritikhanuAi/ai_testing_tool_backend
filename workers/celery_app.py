from celery import Celery
from app.core.config import settings

# Register all database models so mapper relationships resolve in Celery worker processes
import app.apps.websites.models  # noqa
import app.apps.scan_profiles.models  # noqa
import app.apps.test_packs.models  # noqa
import app.apps.test_runs.models  # noqa
import app.apps.reports.models  # noqa

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
    task_time_limit=300,  # 5 minutes max per scan run
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50
)
