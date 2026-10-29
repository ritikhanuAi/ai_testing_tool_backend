import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.apps.test_runs.models import TestRun
from app.apps.test_runs.serializers import TriggerScanRequest
from app.apps.websites.selectors import get_website_by_id
from app.apps.test_packs.selectors import get_test_pack_by_id
from app.apps.scan_profiles.selectors import get_scan_profile_by_id
from app.common.constants import RunStatus, VerificationStatus
from app.core.exceptions import EntityNotFoundException, ScopeViolationException, SchemaValidationException
from test_engine.policy.allowlist import TargetAllowlistValidator
from test_engine.schema.validator import SchemaValidator

async def dispatch_scan_run(db: AsyncSession, request_data: TriggerScanRequest) -> TestRun:
    """Validate target, create TestRun record, and enqueue Celery worker task."""
    website = await get_website_by_id(db, request_data.website_id)
    if not website:
        raise EntityNotFoundException("Website", request_data.website_id)
    if not website.is_active:
        raise ScopeViolationException("This website is inactive and cannot be scanned")
    if not website.is_authorized or website.verification_status != VerificationStatus.VERIFIED:
        raise ScopeViolationException("The website must be authorized and ownership-verified before scanning")

    # 1. Enforce Target Scope Allowlist Policy
    is_allowed, reason = await asyncio.to_thread(
        TargetAllowlistValidator.validate_target,
        target_url=website.base_url,
        allowed_domains=website.allowed_domains,
    )
    if not is_allowed:
        raise ScopeViolationException(f"Target scope validation failed: {reason}")

    # 2. Validate the selected pack and profile before creating a run record.
    raw_yaml = None
    if request_data.test_pack_id:
        pack = await get_test_pack_by_id(db, request_data.test_pack_id)
        if not pack:
            raise EntityNotFoundException("Test pack", request_data.test_pack_id)
        if pack.website_id and pack.website_id != website.id:
            raise ScopeViolationException("The selected test pack belongs to a different website")
        if not pack.is_valid:
            raise SchemaValidationException("The selected test pack is invalid", pack.validation_errors)
        valid, parsed_pack, errors = SchemaValidator.validate_yaml_pack(pack.raw_yaml)
        if not valid or not parsed_pack:
            raise SchemaValidationException("The selected test pack failed pre-flight validation", errors)
        raw_yaml = pack.raw_yaml

    if request_data.scan_profile_id:
        profile = await get_scan_profile_by_id(db, request_data.scan_profile_id)
        if not profile:
            raise EntityNotFoundException("Scan profile", request_data.scan_profile_id)
        profile_payload = {
            "check_functional": profile.check_functional,
            "check_security": profile.check_security,
            "check_performance": profile.check_performance,
            "check_accessibility": profile.check_accessibility,
            "allow_state_changing": profile.allow_state_changing,
            "rate_limit": profile.rate_limit,
            "timeout_seconds": profile.timeout_seconds,
        }
    else:
        profile_payload = {
            "check_functional": True,
            "check_security": True,
            "check_performance": True,
            "check_accessibility": True,
            "allow_state_changing": False,
            "rate_limit": 10,
            "timeout_seconds": 900,
        }

    if not any(profile_payload[key] for key in (
        "check_functional", "check_security", "check_performance", "check_accessibility"
    )):
        raise ScopeViolationException("A scan profile must enable at least one testing pillar")

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
            test_pack_yaml=raw_yaml,
            scan_profile=profile_payload,
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
