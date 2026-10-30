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

from urllib.parse import urlparse
from app.apps.websites.models import Website, CredentialRef
from app.apps.scan_profiles.models import ScanProfile
from app.core.security import encrypt_credential
from app.apps.test_runs.serializers import QuickScanRequest, CustomScanRequest

async def dispatch_quick_scan(db: AsyncSession, request_data: QuickScanRequest) -> TestRun:
    """End-to-end autonomous scan trigger: validates SSRF, auto-provisions website/profile, and queues scan."""
    parsed = urlparse(request_data.url)
    hostname = parsed.hostname or ""
    if not hostname:
        raise ScopeViolationException("Invalid target URL; missing hostname")

    # 1. Enforce SSRF & Private Host Guardrails
    TargetAllowlistValidator.assert_public_host(hostname)

    # 2. Find or auto-provision Website
    from sqlalchemy import select
    clean_base_url = f"{parsed.scheme}://{parsed.netloc}"
    stmt = select(Website).where(Website.base_url == clean_base_url)
    res = await db.execute(stmt)
    website = res.scalar_one_or_none()

    # Determine allowed domains (e.g., both www.apple.com and apple.com)
    parts = hostname.split(".")
    root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else hostname
    target_domains = list(set([hostname, root_domain]))

    if not website:
        site_name = request_data.name or hostname.replace("www.", "").capitalize()
        website = Website(
            name=site_name,
            base_url=clean_base_url,
            allowed_domains=target_domains,
            is_authorized=True,
            is_active=True
        )
        db.add(website)
        await db.flush()
    else:
        # Ensure root domain is present in existing record
        current_allowed = set(website.allowed_domains or [])
        if not set(target_domains).issubset(current_allowed):
            website.allowed_domains = list(current_allowed.union(target_domains))
            await db.flush()

    # 3. Store credentials securely if provided
    credentials_dict = None
    if request_data.credentials and request_data.credentials.enabled:
        cred_payload = request_data.credentials
        raw_pw = cred_payload.password or ""
        encrypted_pw = encrypt_credential(raw_pw)

        # Check existing credential
        cred_stmt = select(CredentialRef).where(
            CredentialRef.website_id == website.id,
            CredentialRef.key_name == "default_login"
        )
        c_res = await db.execute(cred_stmt)
        existing_cred = c_res.scalar_one_or_none()

        if existing_cred:
            existing_cred.username = cred_payload.username
            existing_cred.encrypted_secret = encrypted_pw
        else:
            new_cred = CredentialRef(
                website_id=website.id,
                key_name="default_login",
                username=cred_payload.username,
                encrypted_secret=encrypted_pw,
                description="Auto-stored credentials for autonomous scan"
            )
            db.add(new_cred)

        credentials_dict = {
            "login_url": cred_payload.login_url or request_data.url,
            "username": cred_payload.username,
            "password": raw_pw,
            "auth_method": cred_payload.auth_method,
            "success_indicator": cred_payload.success_indicator
        }
    elif website:
        # Fallback to previously stored credentials for this website
        cred_stmt = select(CredentialRef).where(
            CredentialRef.website_id == website.id,
            CredentialRef.key_name == "default_login"
        )
        c_res = await db.execute(cred_stmt)
        existing_cred = c_res.scalar_one_or_none()
        if existing_cred:
            from app.core.security import decrypt_credential
            try:
                decrypted_pw = decrypt_credential(existing_cred.encrypted_secret)
                credentials_dict = {
                    "login_url": request_data.url,
                    "username": existing_cred.username,
                    "password": decrypted_pw,
                    "auth_method": "form",
                    "success_indicator": None
                }
            except Exception:
                pass

    # 4. Resolve ScanProfile from options
    options_dict = request_data.options.model_dump() if request_data.options else {}
    profile_stmt = select(ScanProfile).where(ScanProfile.name == "Quick Scan Profile")
    p_res = await db.execute(profile_stmt)
    scan_profile = p_res.scalar_one_or_none()

    if scan_profile:
        scan_profile.check_functional = options_dict.get("check_functional", True)
        scan_profile.check_security = options_dict.get("check_security", True)
        scan_profile.check_performance = options_dict.get("check_performance", True)
        scan_profile.check_accessibility = options_dict.get("check_accessibility", True)
        scan_profile.allow_state_changing = options_dict.get("allow_state_changing", False)
        scan_profile.rate_limit = options_dict.get("rate_limit_rps", 10)
        scan_profile.timeout_seconds = options_dict.get("timeout_seconds", 300)
        scan_profile.browser_type = options_dict.get("browser_type", "chromium")
        scan_profile.viewport_width = options_dict.get("viewport_width", 1280)
        scan_profile.viewport_height = options_dict.get("viewport_height", 720)
    else:
        scan_profile = ScanProfile(
            name="Quick Scan Profile",
            description="Auto-generated scan profile for quick autonomous scans",
            check_functional=options_dict.get("check_functional", True),
            check_security=options_dict.get("check_security", True),
            check_performance=options_dict.get("check_performance", True),
            check_accessibility=options_dict.get("check_accessibility", True),
            allow_state_changing=options_dict.get("allow_state_changing", False),
            rate_limit=options_dict.get("rate_limit_rps", 10),
            timeout_seconds=options_dict.get("timeout_seconds", 300),
            browser_type=options_dict.get("browser_type", "chromium"),
            viewport_width=options_dict.get("viewport_width", 1280),
            viewport_height=options_dict.get("viewport_height", 720)
        )
        db.add(scan_profile)
        await db.flush()

    # 5. Create TestRun record
    test_run = TestRun(
        website_id=website.id,
        scan_profile_id=scan_profile.id,
        status=RunStatus.QUEUED,
        started_at=datetime.now(timezone.utc)
    )
    db.add(test_run)
    await db.commit()
    await db.refresh(test_run)

    # 6. Dispatch to Celery background worker
    try:
        from workers.tasks.run_scan import execute_website_scan
        task = execute_website_scan.delay(
            run_id=test_run.id,
            target_url=request_data.url,
            allowed_domains=website.allowed_domains,
            test_pack_yaml=None,
            credentials=credentials_dict,
            options=options_dict
        )
        test_run.celery_task_id = task.id
        await db.commit()
    except Exception:
        # Graceful fallback if Redis/Celery broker is offline in dev
        pass

    return test_run

async def dispatch_custom_scan(db: AsyncSession, request_data: CustomScanRequest) -> TestRun:
    """Execute custom structured JSON test cases: validates SSRF, stores test pack, auto-provisions profile, and queues scan."""
    parsed = urlparse(request_data.url)
    hostname = parsed.hostname or ""
    if not hostname:
        raise ScopeViolationException("Invalid target URL; missing hostname")

    # 1. Enforce SSRF & Private Host Guardrails
    TargetAllowlistValidator.assert_public_host(hostname)

    # 2. Find or auto-provision Website
    from sqlalchemy import select
    clean_base_url = f"{parsed.scheme}://{parsed.netloc}"
    stmt = select(Website).where(Website.base_url == clean_base_url)
    res = await db.execute(stmt)
    website = res.scalar_one_or_none()

    parts = hostname.split(".")
    root_domain = ".".join(parts[-2:]) if len(parts) >= 2 else hostname
    target_domains = list(set([hostname, root_domain]))

    if not website:
        site_name = request_data.name or hostname.replace("www.", "").capitalize()
        website = Website(
            name=site_name,
            base_url=clean_base_url,
            allowed_domains=target_domains,
            is_authorized=True,
            is_active=True
        )
        db.add(website)
        await db.flush()
    else:
        current_allowed = set(website.allowed_domains or [])
        if not set(target_domains).issubset(current_allowed):
            website.allowed_domains = list(current_allowed.union(target_domains))
            await db.flush()

    # 3. Store credentials securely if provided
    credentials_dict = None
    if request_data.credentials and request_data.credentials.enabled:
        cred_payload = request_data.credentials
        raw_pw = cred_payload.password or ""
        encrypted_pw = encrypt_credential(raw_pw)

        cred_stmt = select(CredentialRef).where(
            CredentialRef.website_id == website.id,
            CredentialRef.key_name == "default_login"
        )
        c_res = await db.execute(cred_stmt)
        existing_cred = c_res.scalar_one_or_none()

        if existing_cred:
            existing_cred.username = cred_payload.username
            existing_cred.encrypted_secret = encrypted_pw
        else:
            new_cred = CredentialRef(
                website_id=website.id,
                key_name="default_login",
                username=cred_payload.username,
                encrypted_secret=encrypted_pw,
                description="Auto-stored credentials for custom scan"
            )
            db.add(new_cred)

        credentials_dict = {
            "login_url": cred_payload.login_url or request_data.url,
            "username": cred_payload.username,
            "password": raw_pw,
            "auth_method": cred_payload.auth_method,
            "success_indicator": cred_payload.success_indicator
        }
    elif website:
        # Fallback to previously stored credentials for this website
        cred_stmt = select(CredentialRef).where(
            CredentialRef.website_id == website.id,
            CredentialRef.key_name == "default_login"
        )
        c_res = await db.execute(cred_stmt)
        existing_cred = c_res.scalar_one_or_none()
        if existing_cred:
            from app.core.security import decrypt_credential
            try:
                decrypted_pw = decrypt_credential(existing_cred.encrypted_secret)
                credentials_dict = {
                    "login_url": request_data.url,
                    "username": existing_cred.username,
                    "password": decrypted_pw,
                    "auth_method": "form",
                    "success_indicator": None
                }
            except Exception:
                pass

    # 4. Serialize cases to TestPack
    import yaml
    from app.apps.test_packs.models import TestPack, TestCase
    from test_engine.schema.case import TestPackModel

    pack_id_str = f"PACK-CUSTOM-{int(datetime.now(timezone.utc).timestamp())}"
    pack_title = request_data.name or f"Custom Suite ({len(request_data.cases)} cases)"

    pack_model = TestPackModel(
        pack_id=pack_id_str,
        title=pack_title,
        version="1.0.0",
        target_domain=hostname,
        cases=request_data.cases
    )
    pack_yaml_str = yaml.dump(pack_model.model_dump(mode="json"), sort_keys=False)

    test_pack = TestPack(
        website_id=website.id,
        title=pack_title,
        version="1.0.0",
        raw_yaml=pack_yaml_str,
        cases_count=len(request_data.cases),
        is_valid=True,
        validation_errors=[]
    )
    db.add(test_pack)
    await db.flush()

    # Save individual TestCase rows
    for i, c in enumerate(request_data.cases):
        db_case = TestCase(
            test_pack_id=test_pack.id,
            case_identifier=c.id,
            title=c.title,
            pillar=c.pillar.value if hasattr(c.pillar, "value") else str(c.pillar),
            risk_level=c.risk.value if hasattr(c.risk, "value") else str(c.risk),
            tags=c.tags,
            preconditions=c.preconditions,
            steps=[s.model_dump(mode="json") for s in c.steps],
            assertions=[a.model_dump(mode="json") for a in c.assertions],
            test_data=c.test_data or {},
            order_index=i + 1
        )
        db.add(db_case)
    await db.flush()

    # 5. Resolve ScanProfile
    options_dict = request_data.options.model_dump() if request_data.options else {}
    profile_stmt = select(ScanProfile).where(ScanProfile.name == "Custom Scan Profile")
    p_res = await db.execute(profile_stmt)
    scan_profile = p_res.scalar_one_or_none()

    if scan_profile:
        scan_profile.check_functional = options_dict.get("check_functional", True)
        scan_profile.check_security = options_dict.get("check_security", False)
        scan_profile.check_performance = options_dict.get("check_performance", False)
        scan_profile.check_accessibility = options_dict.get("check_accessibility", False)
        scan_profile.allow_state_changing = options_dict.get("allow_state_changing", False)
        scan_profile.rate_limit = options_dict.get("rate_limit_rps", 10)
        scan_profile.timeout_seconds = options_dict.get("timeout_seconds", 300)
        scan_profile.browser_type = options_dict.get("browser_type", "chromium")
        scan_profile.viewport_width = options_dict.get("viewport_width", 1280)
        scan_profile.viewport_height = options_dict.get("viewport_height", 720)
    else:
        scan_profile = ScanProfile(
            name="Custom Scan Profile",
            description="Auto-generated scan profile for custom test suites",
            check_functional=options_dict.get("check_functional", True),
            check_security=options_dict.get("check_security", False),
            check_performance=options_dict.get("check_performance", False),
            check_accessibility=options_dict.get("check_accessibility", False),
            allow_state_changing=options_dict.get("allow_state_changing", False),
            rate_limit=options_dict.get("rate_limit_rps", 10),
            timeout_seconds=options_dict.get("timeout_seconds", 300),
            browser_type=options_dict.get("browser_type", "chromium"),
            viewport_width=options_dict.get("viewport_width", 1280),
            viewport_height=options_dict.get("viewport_height", 720)
        )
        db.add(scan_profile)
        await db.flush()

    # 6. Create TestRun record
    test_run = TestRun(
        website_id=website.id,
        scan_profile_id=scan_profile.id,
        test_pack_id=test_pack.id,
        status=RunStatus.QUEUED,
        started_at=datetime.now(timezone.utc)
    )
    db.add(test_run)
    await db.commit()
    await db.refresh(test_run)

    # 7. Dispatch to Celery background worker
    try:
        from workers.tasks.run_scan import execute_website_scan
        task = execute_website_scan.delay(
            run_id=test_run.id,
            target_url=request_data.url,
            allowed_domains=website.allowed_domains,
            test_pack_yaml=pack_yaml_str,
            credentials=credentials_dict,
            options=options_dict
        )
        test_run.celery_task_id = task.id
        await db.commit()
    except Exception:
        pass

    return test_run

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
    credentials_dict = None
    cred_stmt = select(CredentialRef).where(
        CredentialRef.website_id == website.id,
        CredentialRef.key_name == "default_login"
    )
    c_res = await db.execute(cred_stmt)
    existing_cred = c_res.scalar_one_or_none()
    if existing_cred:
        from app.core.security import decrypt_credential
        try:
            decrypted_pw = decrypt_credential(existing_cred.encrypted_secret)
            credentials_dict = {
                "login_url": website.base_url,
                "username": existing_cred.username,
                "password": decrypted_pw,
                "auth_method": "form",
                "success_indicator": None
            }
        except Exception:
            pass

    try:
        from workers.tasks.run_scan import execute_website_scan
        task = execute_website_scan.delay(
            run_id=test_run.id,
            target_url=website.base_url,
            allowed_domains=website.allowed_domains,
            test_pack_yaml=raw_yaml,
            scan_profile=profile_payload,
            credentials=credentials_dict,
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
    test_run.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(test_run)
    return test_run
