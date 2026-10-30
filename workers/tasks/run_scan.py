import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse
from workers.celery_app import celery_app
from test_engine.browser.pool import BrowserPool
from test_engine.browser.context import ContextFactory
from test_engine.pillars.functional.runner import FunctionalScanner
from test_engine.crawl import CrawlResult, SiteCrawler, redact_url
from test_engine.pillars.security.runner import SecurityScanner
from test_engine.pillars.performance.runner import PerformanceScanner
from test_engine.pillars.performance.vitals import CoreWebVitalsScanner
from test_engine.pillars.accessibility.runner import AccessibilityScanner
from test_engine.pillars.base import PillarScanResult
from test_engine.scoring.aggregator import ReportAggregator
from test_engine.schema.validator import SchemaValidator
from app.common.constants import PillarType, RunStatus, FindingSeverity
from app.core.config import settings

# Ensure all SQLAlchemy models are registered in the Declarative Base registry
import app.apps.websites.models  # noqa
import app.apps.scan_profiles.models  # noqa
import app.apps.test_packs.models  # noqa
import app.apps.test_runs.models  # noqa
import app.apps.reports.models  # noqa

logger = logging.getLogger("workers.tasks.run_scan")

async def _persist_scan_start(run_id: str) -> None:
    """Mark test run as running in the database."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.apps.test_runs.models import TestRun
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            stmt = select(TestRun).where(TestRun.id == run_id)
            result = await db.execute(stmt)
            run = result.scalar_one_or_none()
            if run:
                run.status = RunStatus.RUNNING
                run.started_at = datetime.now(timezone.utc)
                await db.commit()
    except Exception as e:
        logger.warning(f"Could not update start status for run {run_id}: {e}")

async def _persist_scan_success(run_id: str, report_data: Dict[str, Any], artifacts: Optional[List[Dict[str, Any]]] = None) -> None:
    """Save report summary, artifacts, and mark test run as completed."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.apps.test_runs.models import TestRun, RunArtifact
        from app.apps.reports.services import save_report
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            await save_report(db, run_id, report_data)
            stmt = select(TestRun).where(TestRun.id == run_id)
            result = await db.execute(stmt)
            run = result.scalar_one_or_none()
            if run:
                run.status = RunStatus.COMPLETED
                run.completed_at = datetime.now(timezone.utc)
                if artifacts:
                    for art in artifacts:
                        abs_p = os.path.join(settings.STORAGE_DIR, art["relative_path"])
                        size = os.path.getsize(abs_p) if os.path.exists(abs_p) else 0
                        db.add(RunArtifact(
                            test_run_id=run_id,
                            artifact_type=art["artifact_type"],
                            relative_path=art["relative_path"],
                            file_name=art["file_name"],
                            file_size_bytes=size,
                            mime_type=art.get("mime_type", "application/octet-stream")
                        ))
                await db.commit()
    except Exception as e:
        logger.error(f"Failed to persist scan success for run {run_id}: {e}", exc_info=True)

async def _is_cancelled(run_id: str) -> bool:
    """Check if cancellation was requested via Redis flag."""
    try:
        import redis.asyncio as aioredis
        from app.core.config import settings
        r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        val = await r.get(f"cancel:{run_id}")
        await r.aclose()
        return val is not None
    except Exception:
        return False

async def _persist_scan_failure(run_id: str, error_message: str, is_cancelled: bool = False) -> None:
    """Mark test run as failed or cancelled with diagnostic message."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.apps.test_runs.models import TestRun
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            stmt = select(TestRun).where(TestRun.id == run_id)
            result = await db.execute(stmt)
            run = result.scalar_one_or_none()
            if run:
                run.status = RunStatus.CANCELLED if is_cancelled else RunStatus.FAILED
                run.error_message = error_message
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()
    except Exception as e:
        logger.error(f"Failed to persist scan failure for run {run_id}: {e}", exc_info=True)

async def _perform_authentication(page, credentials: Dict[str, Any]) -> None:
    """Pre-flight login routine using supplied credentials."""
    login_url = credentials.get("login_url")
    username = credentials.get("username")
    password = credentials.get("password")
    if not (login_url or username or password):
        return

    try:
        if login_url:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=25000)

        if username:
            user_input = page.locator('input[type="email"], input[name*="user"], input[name*="email"], input[id*="user"], input[id*="email"]').first
            if await user_input.count() > 0:
                await user_input.fill(username)

        if password:
            pass_input = page.locator('input[type="password"]').first
            if await pass_input.count() > 0:
                await pass_input.fill(password)

        submit_btn = page.locator('button[type="submit"], input[type="submit"], button:has-text("Login"), button:has-text("Sign in")').first
        if await submit_btn.count() > 0:
            await submit_btn.click()
            await page.wait_for_timeout(2000)
    except Exception as auth_err:
        logger.warning(f"Authentication flow encountered non-fatal error: {auth_err}")

async def _discover_and_generate_ai_tests(page, options: Dict[str, Any]) -> List[Any]:
    """Inspect page landmarks and generate structured test cases via LLM."""
    try:
        from ai_engine.clients.factory import LLMClientFactory
        from ai_engine.prompts.test_generation import SYSTEM_PROMPT, build_case_generation_prompt
        from ai_engine.parsers.schema_enforcer import SchemaEnforcer

        landmarks = await page.evaluate("""() => {
            const buttons = Array.from(document.querySelectorAll('button, a[href], input[type="submit"]'))
                .map(el => (el.innerText || el.getAttribute('placeholder') || el.value || '').trim())
                .filter(Boolean).slice(0, 8);
            const inputs = Array.from(document.querySelectorAll('input:not([type="hidden"]), select, textarea'))
                .map(el => (el.name || el.id || el.getAttribute('placeholder') || '').trim())
                .filter(Boolean).slice(0, 8);
            return { buttons, inputs, title: document.title || '' };
        }""")

        page_desc = f"Page Title: {landmarks.get('title', '')}. Interactive buttons: {', '.join(landmarks.get('buttons', []))}"
        form_fields = ", ".join(landmarks.get("inputs", []))
        goals = options.get("ai_test_goals") or ""
        if goals:
            page_desc += f" User testing goal: {goals}"

        client = LLMClientFactory.get_client()
        prompt = build_case_generation_prompt(page_desc, form_fields)
        raw_response = await client.generate(prompt=prompt, system_prompt=SYSTEM_PROMPT)
        valid, case_model, _ = SchemaEnforcer.parse_test_case(raw_response)
        if valid and case_model:
            return [case_model]
    except Exception as ai_err:
        logger.warning(f"Autonomous AI test generation skipped: {ai_err}")
    return []

async def _enrich_findings_with_ai(findings: List[Any]) -> None:
    """Diagnose high/critical findings and attach root-cause explanations."""
    try:
        from ai_engine.prompts.failure_explainer import AIFailureExplainer
        for f in findings:
            if getattr(f, "severity", None) in [FindingSeverity.CRITICAL, FindingSeverity.HIGH]:
                diagnosis = await AIFailureExplainer.explain_failure(
                    test_title=f.title,
                    failed_step_or_assert=f.selector_or_url or f.title,
                    error_message=f.description
                )
                if diagnosis and diagnosis.get("suggested_fix"):
                    root_cause = diagnosis.get("root_cause", "")
                    fix = diagnosis.get("suggested_fix", "")
                    f.suggested_fix = f"AI Diagnosis: {root_cause}\nFix: {fix}" if root_cause else fix
    except Exception as err:
        logger.warning(f"AI failure explainer non-fatal error: {err}")

async def _async_execute_scan(
    run_id: str,
    target_url: str,
    allowed_domains: List[str],
    test_pack_yaml: Optional[str] = None,
    cookies: Optional[List[Dict[str, Any]]] = None,
    credentials: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    scan_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Async execution routine inside Playwright worker."""
    from app.common.progress import ScanProgressTracker
    await _persist_scan_start(run_id)
    await ScanProgressTracker.log(run_id, "INITIALIZING", f"Starting automated test scan on {redact_url(target_url)}", 5)

    opts = options or {}
    profile = {
        "check_functional": True,
        "check_security": True,
        "check_performance": True,
        "check_accessibility": True,
        "allow_state_changing": False,
        "rate_limit": settings.DEFAULT_SCAN_RATE_LIMIT,
        "timeout_seconds": 900,
        **opts,
        **(scan_profile or {}),
    }
    creds = credentials or profile.get("credentials")

    record_video = bool(profile.get("record_video", False))
    record_trace = bool(profile.get("record_trace", True))
    is_mobile = bool(profile.get("is_mobile", False))
    browser_type = profile.get("browser_type", "chromium")

    temp_video_dir = None
    target_video_rel = f"videos/{run_id}.webm"
    target_video_abs = os.path.join(settings.STORAGE_DIR, target_video_rel)
    if record_video:
        temp_video_dir = os.path.join(settings.STORAGE_DIR, "videos", f"temp_{run_id}")
        os.makedirs(temp_video_dir, exist_ok=True)

    target_trace_rel = f"traces/{run_id}_trace.zip"
    target_trace_abs = os.path.join(settings.STORAGE_DIR, target_trace_rel)
    os.makedirs(os.path.join(settings.STORAGE_DIR, "traces"), exist_ok=True)
    os.makedirs(os.path.join(settings.STORAGE_DIR, "videos"), exist_ok=True)

    context = None
    page = None
    try:
        browser = await BrowserPool.get_browser(browser_type=browser_type)
        await ScanProgressTracker.log(run_id, "INITIALIZING", f"Created isolated Playwright {browser_type} context with ScopeGuard", 10)
        context = await ContextFactory.create_isolated_context(
            browser=browser,
            allowed_domains=set(allowed_domains),
            cookies=cookies,
            viewport_width=profile.get("viewport_width"),
            viewport_height=profile.get("viewport_height"),
            request_rate_limit=int(profile["rate_limit"]),
            target_url=target_url,
            record_video_dir=temp_video_dir,
            is_mobile=is_mobile,
        )
        if record_trace:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        await CoreWebVitalsScanner.install_observer(context)
        page = await context.new_page()

        if profile.get("viewport_width") and profile.get("viewport_height"):
            await page.set_viewport_size({"width": profile["viewport_width"], "height": profile["viewport_height"]})

        # Pre-flight authentication if credentials supplied
        if creds:
            await ScanProgressTracker.log(run_id, "AUTHENTICATING", f"Executing authentication flow on {creds.get('login_url', target_url)}", 12)
            await _perform_authentication(page, creds)

        # Discover and visit safe, in-scope links starting from landing URL (e.g., authenticated dashboard)
        start_crawl_url = page.url if page.url and page.url != "about:blank" else target_url
        await ScanProgressTracker.log(run_id, "CRAWLING", "Crawling in-scope routes and analyzing site structure...", 15)
        crawl_result = await SiteCrawler.crawl(
            page,
            start_crawl_url,
            set(allowed_domains),
            max_pages=100,
            max_depth=5,
            # Reserve time for the four pillar scanners and report persistence.
            timeout_seconds=max(5, int(profile["timeout_seconds"] * 0.6)),
            request_rate_limit=int(profile["rate_limit"]),
            cancellation_check=lambda: _is_cancelled(run_id),
        )

        # Check cancellation before initial run
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled before start")

        # 1. Functional Pillar (the authored test pack runs against its starting page).
        test_cases = []
        if test_pack_yaml:
            valid, pack, errors = SchemaValidator.validate_yaml_pack(test_pack_yaml)
            if not valid or not pack:
                raise ValueError(f"Test pack failed worker-side validation: {errors}")
            test_cases = pack.cases
        elif profile.get("use_ai_generation", True) and profile.get("ai_test_goals"):
            await ScanProgressTracker.log(run_id, "FUNCTIONAL", "Synthesizing AI test scenarios for target goals...", 22)
            ai_cases = await _discover_and_generate_ai_tests(page, profile)
            test_cases.extend(ai_cases)

        if profile["check_functional"]:
            cases_label = f"{len(test_cases)} authored test cases" if test_cases else "autonomous form discovery & AI interaction exploration"
            await ScanProgressTracker.log(run_id, "FUNCTIONAL", f"Running Functional Pillar: Executing {cases_label}...", 25)
            response = None
            try:
                response = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                err_str = str(e)
                if "ERR_NAME_NOT_RESOLVED" in err_str:
                    parsed_tgt = urlparse(target_url)
                    if parsed_tgt.hostname:
                        fallback_host = parsed_tgt.hostname[4:] if parsed_tgt.hostname.startswith("www.") else f"www.{parsed_tgt.hostname}"
                        fallback_url = target_url.replace(parsed_tgt.hostname, fallback_host)
                        try:
                            response = await page.goto(fallback_url, wait_until="domcontentloaded", timeout=30000)
                            target_url = fallback_url
                        except Exception:
                            pass
            func_result = await FunctionalScanner().scan(
                page,
                target_url,
                test_cases=test_cases,
                run_id=run_id,
                allow_state_changing=bool(profile["allow_state_changing"]),
                crawl_result=crawl_result,
                initial_status_code=response.status if response else None,
            )
            if profile.get("ai_explain_failures", True) and func_result.findings:
                await ScanProgressTracker.log(run_id, "FUNCTIONAL", "Diagnosing functional failures with AI...", 40)
                await _enrich_findings_with_ai(func_result.findings)
            await ScanProgressTracker.log(run_id, "FUNCTIONAL", f"Functional pillar completed (Score: {func_result.score}/100, Findings: {len(func_result.findings)})", 48)
        else:
            func_result = PillarScanResult(
                pillar=PillarType.FUNCTIONAL,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after functional pillar")

        # 2. Security Pillar, using observations from every crawled page.
        if profile["check_security"]:
            await ScanProgressTracker.log(run_id, "SECURITY", "Starting Security Pillar: Auditing headers, cookies, TLS & exposures...", 50)
            sec_result = await SecurityScanner().scan(
                page,
                target_url,
                crawl_result=crawl_result,
                request_rate_limit=int(profile["rate_limit"]),
            )
            await ScanProgressTracker.log(run_id, "SECURITY", f"Security pillar completed (Score: {sec_result.score}/100, Findings: {len(sec_result.findings)})", 68)
        else:
            sec_result = PillarScanResult(
                pillar=PillarType.SECURITY,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after security pillar")

        # 3. Performance Pillar, with page-level metrics plus site p75 summaries.
        if profile["check_performance"]:
            await ScanProgressTracker.log(run_id, "PERFORMANCE", "Starting Performance Pillar: Measuring navigation timings and Core Web Vitals...", 70)
            perf_result = await PerformanceScanner().scan(page, target_url, crawl_result=crawl_result)
            await ScanProgressTracker.log(run_id, "PERFORMANCE", f"Performance pillar completed (Score: {perf_result.score}/100, Findings: {len(perf_result.findings)})", 82)
        else:
            perf_result = PillarScanResult(
                pillar=PillarType.PERFORMANCE,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after performance pillar")

        # 4. Accessibility & UX Pillar (Axe-core WCAG + AI Code Fixes + Mobile + Multimodal Vision)
        if profile["check_accessibility"]:
            await ScanProgressTracker.log(run_id, "ACCESSIBILITY", "Starting Accessibility Pillar: Running Axe-core WCAG rules, responsive audits & AI visual review...", 85)
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            a11y_result = await AccessibilityScanner().scan(
                page,
                target_url,
                run_id=run_id,
                comparison_paths=func_result.metrics.get("visual_comparison_paths", []),
                crawl_result=crawl_result,
            )
            await ScanProgressTracker.log(run_id, "ACCESSIBILITY", f"Accessibility pillar completed (Score: {a11y_result.score}/100, Findings: {len(a11y_result.findings)})", 95)
        else:
            a11y_result = PillarScanResult(
                pillar=PillarType.ACCESSIBILITY,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        # Aggregate 4 Pillars
        await ScanProgressTracker.log(run_id, "AGGREGATING", "Aggregating 4-pillar findings and generating deterministic report...", 97)
        unified_report = ReportAggregator.aggregate(
            run_id=run_id,
            target_url=target_url,
            pillar_results=[func_result, sec_result, perf_result, a11y_result],
        )

        # Collect generated trace and video artifacts
        artifacts: List[Dict[str, Any]] = []

        if record_trace and context:
            try:
                await context.tracing.stop(path=target_trace_abs)
                if os.path.exists(target_trace_abs) and os.path.getsize(target_trace_abs) > 0:
                    artifacts.append({
                        "artifact_type": "trace",
                        "relative_path": target_trace_rel,
                        "file_name": f"{run_id}_trace.zip",
                        "mime_type": "application/zip",
                    })
            except Exception as trace_err:
                logger.warning(f"Failed to stop/save Playwright trace for {run_id}: {trace_err}")

        if record_video and page:
            try:
                video_obj = page.video
                await page.close()
                page = None
                if video_obj:
                    await video_obj.save_as(target_video_abs)
                    if os.path.exists(target_video_abs) and os.path.getsize(target_video_abs) > 0:
                        artifacts.append({
                            "artifact_type": "video",
                            "relative_path": target_video_rel,
                            "file_name": f"{run_id}.webm",
                            "mime_type": "video/webm",
                        })
            except Exception as vid_err:
                logger.warning(f"Failed to save Playwright video for {run_id}: {vid_err}")

        report_dict = unified_report.model_dump()
        report_dict["artifacts"] = {art["artifact_type"]: art["relative_path"] for art in artifacts}
        await _persist_scan_success(run_id, report_dict, artifacts=artifacts)
        await ScanProgressTracker.log(
            run_id, "COMPLETED",
            f"Audit finished successfully! Overall Score: {unified_report.overall_score}/100, Grade: {unified_report.grade}",
            100
        )
        return report_dict

    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task and current_task.cancelling():
            raise
        logger.info(f"Scan run {run_id} aborted due to cancellation.")
        await _persist_scan_failure(run_id, "Scan cancelled by user request", is_cancelled=True)
        return {"status": "CANCELLED", "run_id": run_id}
    except Exception as exc:
        await _persist_scan_failure(run_id, str(exc), is_cancelled=False)
        raise
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if temp_video_dir and os.path.exists(temp_video_dir):
            import shutil
            shutil.rmtree(temp_video_dir, ignore_errors=True)

async def _execute_with_timeout(
    run_id: str,
    target_url: str,
    allowed_domains: List[str],
    test_pack_yaml: Optional[str],
    cookies: Optional[List[Dict[str, Any]]],
    credentials: Optional[Dict[str, Any]],
    options: Optional[Dict[str, Any]],
    scan_profile: Optional[Dict[str, Any]],
    timeout_seconds: int,
) -> Dict[str, Any]:
    from app.common.progress import ScanProgressTracker
    try:
        return await asyncio.wait_for(
            _async_execute_scan(
                run_id=run_id,
                target_url=target_url,
                allowed_domains=allowed_domains,
                test_pack_yaml=test_pack_yaml,
                cookies=cookies,
                credentials=credentials,
                options=options,
                scan_profile=scan_profile,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        err_msg = f"Scan exceeded its {timeout_seconds}-second timeout limit"
        logger.error(f"Scan {run_id} failed: {err_msg}")
        try:
            await _persist_scan_failure(run_id, err_msg)
            await ScanProgressTracker.log(run_id, "FAILED", err_msg, 100, level="ERROR")
        except Exception as e:
            logger.error(f"Error persisting scan failure for {run_id}: {e}")
        return {"status": "FAILED", "run_id": run_id, "error": "scan_timeout"}

@celery_app.task(bind=True, name="workers.tasks.run_scan.execute_website_scan")
def execute_website_scan(
    self,
    run_id: str,
    target_url: str,
    allowed_domains: List[str],
    test_pack_yaml: Optional[str] = None,
    cookies: Optional[List[Dict[str, Any]]] = None,
    credentials: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    scan_profile: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
):
    """Celery task entrypoint."""
    logger.info(f"Starting automated scan task for run {run_id} on {redact_url(target_url)}")
    effective_profile = {**(options or {}), **(scan_profile or {})}
    # Allow at least 300s (5 minutes) so that crawler + multi-step AI ReAct journey can complete
    raw_timeout = int(effective_profile.get("timeout_seconds") or 300)
    timeout_seconds = max(300, raw_timeout)
    return asyncio.run(_execute_with_timeout(
        run_id=run_id,
        target_url=target_url,
        allowed_domains=allowed_domains,
        test_pack_yaml=test_pack_yaml,
        cookies=cookies,
        credentials=credentials,
        options=options,
        scan_profile=scan_profile,
        timeout_seconds=timeout_seconds,
    ))
