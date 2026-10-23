from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.apps.test_runs.models import TestRun
from app.apps.test_runs.serializers import TriggerScanRequest
from app.apps.websites.selectors import get_website_by_id
from app.apps.test_packs.selectors import get_test_pack_by_id
from app.common.constants import RunStatus
from app.core.exceptions import EntityNotFoundException, ScopeViolationException
from test_engine.policy.allowlist import TargetAllowlistValidator

async def dispatch_scan_run(db: AsyncSession, request_data: TriggerScanRequest) -> TestRun:
    """Validate target, create TestRun record, and enqueue Celery worker task."""
    website = await get_website_by_id(db, request_data.website_id)
    if not website:
        raise EntityNotFoundException("Website", request_data.website_id)

    # 1. Enforce Target Scope Allowlist Policy
    is_allowed, reason = TargetAllowlistValidator.validate_target(
        target_url=website.base_url,
        allowed_domains=website.allowed_domains
    )
    if not is_allowed:
        raise ScopeViolationException(f"Target scope validation failed: {reason}")

    # 2. Retrieve optional test pack
    raw_yaml = None
    if request_data.test_pack_id:
        pack = await get_test_pack_by_id(db, request_data.test_pack_id)
        if pack and pack.is_valid:
            raw_yaml = pack.raw_yaml

    # 3. Create TestRun record
    test_run = TestRun(
        website_id=website.id,
        scan_profile_id=request_data.scan_profile_id,
        test_pack_id=request_data.test_pack_id,
        status=RunStatus.QUEUED,
        started_at=datetime.now(timezone.utc)
    )
    db.add(test_run)
    await db.commit()
    await db.refresh(test_run)

    # 4. Enqueue Celery task asynchronously
    try:
        from workers.tasks.run_scan import execute_website_scan
        task = execute_website_scan.delay(
            run_id=test_run.id,
            target_url=website.base_url,
            allowed_domains=website.allowed_domains,
            test_pack_yaml=raw_yaml
        )
        test_run.celery_task_id = task.id
        await db.commit()
    except Exception:
        # Fallback if Celery broker is currently offline during local scaffolding
        pass

    return test_run

async def cancel_scan_run(db: AsyncSession, run_id: str) -> TestRun:
    """Request immediate cancellation of a queued or running scan."""
    from app.apps.test_runs.selectors import get_test_run_by_id
    test_run = await get_test_run_by_id(db, run_id)
    if not test_run:
        raise EntityNotFoundException("TestRun", run_id)

    # 1. Set cancellation signal in Redis for active Playwright worker
    try:
        import redis.asyncio as aioredis
        from app.core.config import settings
        r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        await r.set(f"cancel:{run_id}", "1", ex=3600)
        await r.aclose()
    except Exception:
        pass

    # 2. Revoke Celery task if queued
    if test_run.celery_task_id:
        try:
            from workers.celery_app import celery_app
            celery_app.control.revoke(test_run.celery_task_id, terminate=True)
        except Exception:
            pass

    test_run.status = RunStatus.CANCELLED
    test_run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(test_run)
    return test_run
