import asyncio
import hashlib
import json
import logging
import os
import time
import shutil
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse
from workers.celery_app import celery_app
from test_engine.browser.pool import BrowserPool
from test_engine.browser.context import ContextFactory
from test_engine.browser.readiness import wait_for_page_ready
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


def _matrix_checkpoint_keys(run_id: str, target_url: str) -> tuple[str, str]:
    page_hash = hashlib.sha1(target_url.encode()).hexdigest()[:16]
    base_key = f"matrix_resume:{run_id}:{page_hash}"
    return f"{base_key}:state", f"{base_key}:cases"


async def _load_matrix_checkpoint(run_id: str, target_url: str) -> Optional[Dict[str, Any]]:
    """Load the persisted cursor and completed cases for one page matrix."""
    import redis.asyncio as aioredis

    state_key, cases_key = _matrix_checkpoint_keys(run_id, target_url)
    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    try:
        raw_state = await client.hgetall(state_key)
        if not raw_state:
            return None
        state = {
            (key.decode() if isinstance(key, bytes) else key):
            (value.decode() if isinstance(value, bytes) else value)
            for key, value in raw_state.items()
        }
        raw_cases = await client.lrange(cases_key, 0, -1)
        cases = []
        for raw_case in raw_cases:
            try:
                cases.append(json.loads(raw_case.decode() if isinstance(raw_case, bytes) else raw_case))
            except (TypeError, ValueError):
                continue
        executed = int(state.get("executed_cases", 0))
        next_case_index = int(state.get("next_case_index", 0))
        try:
            manifest = json.loads(state.get("manifest") or "[]")
        except ValueError:
            manifest = []
        if len(cases) != executed or next_case_index != executed or not isinstance(manifest, list) or not manifest:
            logger.warning("Discarding inconsistent interaction checkpoint for %s", redact_url(target_url))
            await client.delete(state_key, cases_key)
            return None
        return {
            "manifest_fingerprint": state.get("manifest_fingerprint"),
            "manifest": manifest,
            "next_case_index": next_case_index,
            "executed_cases": executed,
            "passed_cases": int(state.get("passed_cases", 0)),
            "failed_cases": int(state.get("failed_cases", 0)),
            "blocked_cases": int(state.get("blocked_cases", 0)),
            "test_cases": cases,
        }
    finally:
        await client.aclose()


async def _save_matrix_checkpoint(
    run_id: str,
    target_url: str,
    checkpoint: Dict[str, Any],
) -> None:
    """Atomically append one case and advance the page cursor in Redis."""
    import redis.asyncio as aioredis

    state_key, cases_key = _matrix_checkpoint_keys(run_id, target_url)
    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    try:
        save_script = """
            local fingerprint = redis.call('HGET', KEYS[1], 'manifest_fingerprint')
            if fingerprint ~= ARGV[1] then
                redis.call('DEL', KEYS[1], KEYS[2])
            end
            if ARGV[9] == '1' then
                redis.call('HSET', KEYS[1],
                    'manifest_fingerprint', ARGV[1],
                    'next_case_index', '0',
                    'executed_cases', '0',
                    'passed_cases', '0',
                    'failed_cases', '0',
                    'blocked_cases', '0',
                    'manifest', ARGV[10])
                redis.call('EXPIRE', KEYS[1], ARGV[8])
                redis.call('EXPIRE', KEYS[2], ARGV[8])
                return 1
            end
            local cursor = tonumber(redis.call('HGET', KEYS[1], 'next_case_index') or '0')
            local next_cursor = tonumber(ARGV[2])
            if next_cursor <= cursor then return 0 end
            if next_cursor ~= cursor + 1 then
                return redis.error_reply('interaction checkpoint cursor is not sequential')
            end
            redis.call('RPUSH', KEYS[2], ARGV[7])
            redis.call('HSET', KEYS[1],
                'manifest_fingerprint', ARGV[1],
                'next_case_index', ARGV[2],
                'executed_cases', ARGV[3],
                'passed_cases', ARGV[4],
                'failed_cases', ARGV[5],
                'blocked_cases', ARGV[6])
            if ARGV[10] ~= '' then redis.call('HSET', KEYS[1], 'manifest', ARGV[10]) end
            redis.call('EXPIRE', KEYS[1], ARGV[8])
            redis.call('EXPIRE', KEYS[2], ARGV[8])
            return 1
        """
        await client.eval(
            save_script,
            2,
            state_key,
            cases_key,
            checkpoint["manifest_fingerprint"],
            checkpoint["next_case_index"],
            checkpoint["executed_cases"],
            checkpoint["passed_cases"],
            checkpoint["failed_cases"],
            checkpoint["blocked_cases"],
            json.dumps(checkpoint.get("case"), ensure_ascii=False) if checkpoint.get("case") is not None else "",
            30 * 24 * 60 * 60,
            "1" if checkpoint.get("initialize") else "0",
            json.dumps(checkpoint.get("manifest"), ensure_ascii=False) if checkpoint.get("manifest") is not None else "",
        )
    finally:
        await client.aclose()


async def _clear_matrix_checkpoints(run_id: str) -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    try:
        keys = [key async for key in client.scan_iter(match=f"matrix_resume:{run_id}:*")]
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


def _matrix_batch_can_resume(matrix_pages: List[Dict[str, Any]]) -> bool:
    incomplete = [page for page in matrix_pages if not page.get("complete")]
    if not incomplete:
        return False
    resumable_reasons = (
        "configured batch limit",
        "page interaction time budget ended",
    )
    for page in incomplete:
        reason = str(page.get("interruption_reason") or "").lower()
        discovery_errors = [str(error).lower() for error in page.get("discovery_errors") or []]
        discovery_errors_are_retryable = all(
            (
                error.startswith("could not inspect revealed dropdown ")
                or error.startswith("could not inspect controls revealed by ")
            )
            and "timeout" in error
            for error in discovery_errors
        )
        if (
            int(page.get("pending_cases") or 0) <= 0
            or not any(marker in reason for marker in resumable_reasons)
            or not page.get("discovery_complete", True)
            or (discovery_errors and not discovery_errors_are_retryable)
            or page.get("unsupported_controls")
            or page.get("undiscovered_custom_dropdowns")
            or page.get("resume_manifest_changed")
        ):
            return False
    return True


async def _mark_scan_continuing(run_id: str, message: str) -> None:
    from app.common.progress import ScanProgressTracker
    from app.core.worker_database import AsyncSessionLocal
    from app.apps.test_runs.models import TestRun
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(TestRun).where(TestRun.id == run_id))
        run = result.scalar_one_or_none()
        if run:
            run.status = RunStatus.RUNNING
            run.error_message = message
            await db.commit()
    await ScanProgressTracker.log(run_id, "FUNCTIONAL", message, 30, level="INFO")

async def _persist_scan_start(run_id: str) -> None:
    """Mark test run as running in the database."""
    try:
        from app.core.worker_database import AsyncSessionLocal
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

async def _persist_scan_success(
    run_id: str,
    report_data: Dict[str, Any],
    artifacts: Optional[List[Dict[str, Any]]] = None,
    note: Optional[str] = None,
) -> None:
    """Save report summary, artifacts, and mark test run as completed."""
    try:
        from app.core.worker_database import AsyncSessionLocal
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
                run.error_message = note
                # Denormalize pillar scores onto the run for fast list filtering
                pillar_results = report_data.get("pillar_results", {})
                run.functional_score = (pillar_results.get("functional") or {}).get("score")
                run.security_score = (pillar_results.get("security") or {}).get("score")
                run.performance_score = (pillar_results.get("performance") or {}).get("score")
                run.accessibility_score = (pillar_results.get("accessibility") or {}).get("score")
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

async def _persist_partial_report(run_id: str, partial: Dict[str, Any], timeout_seconds: int) -> Dict[str, Any]:
    """Save a report from the pillars that finished before the time limit.

    Pillars that did not finish are marked SKIPPED (so they don't count toward the
    grade) and the report is forced to "not passed", because it is incomplete.
    """
    from app.common.progress import ScanProgressTracker

    reason = f"Not run: the scan reached its {timeout_seconds}-second time limit first"
    pillar_results: List[PillarScanResult] = []
    unfinished: List[str] = []
    for pillar in PillarType:
        result = partial.get(pillar)
        if result is None:
            unfinished.append(pillar.value)
            result = PillarScanResult(
                pillar=pillar,
                score=0.0,
                status="SKIPPED",
                metrics={"skipped": True, "timed_out": True, "reason": reason},
            )
        pillar_results.append(result)

    target_url = partial.get("target_url", "")
    report = ReportAggregator.aggregate(run_id=run_id, target_url=target_url, pillar_results=pillar_results)
    report_dict = report.model_dump()
    report_dict["passed"] = False
    report_dict["incomplete"] = True
    report_dict["incomplete_reason"] = reason
    report_dict["unfinished_pillars"] = unfinished
    report_dict["artifacts"] = {}
    report_dict["test_case_mode"] = partial.get("test_case_mode", "ai")
    report_dict["ai_used"] = report_dict["test_case_mode"] != "human"
    functional = partial.get(PillarType.FUNCTIONAL)
    report_dict["test_cases"] = functional.metrics.get("executed_test_cases", []) if functional else []

    note = (
        f"Partial report: time limit of {timeout_seconds}s reached. "
        f"Unfinished checks: {', '.join(unfinished)}. Increase the scan profile timeout for a full report."
    )
    await _persist_scan_success(run_id, report_dict, note=note)
    await ScanProgressTracker.log(run_id, "COMPLETED", note, 100, level="WARNING")
    logger.warning(f"Scan {run_id}: {note}")
    return {"status": "PARTIAL", "run_id": run_id, "unfinished_pillars": unfinished}

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
        from app.core.worker_database import AsyncSessionLocal
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


async def _persist_llm_usage(run_id: str, usage: Any) -> None:
    """Add one Celery batch's provider-reported token usage to its test run."""
    if not usage.calls:
        return

    try:
        from sqlalchemy import func, select, update
        from app.core.worker_database import AsyncSessionLocal
        from app.apps.test_runs.models import TestRun

        updates = {
            "llm_call_count": func.coalesce(TestRun.llm_call_count, 0) + usage.calls,
            "llm_usage_reported_calls": (
                func.coalesce(TestRun.llm_usage_reported_calls, 0) + usage.reported_calls
            ),
            "llm_prompt_tokens": func.coalesce(TestRun.llm_prompt_tokens, 0) + usage.prompt_tokens,
            "llm_completion_tokens": (
                func.coalesce(TestRun.llm_completion_tokens, 0) + usage.completion_tokens
            ),
            "llm_reasoning_tokens": (
                func.coalesce(TestRun.llm_reasoning_tokens, 0) + usage.reasoning_tokens
            ),
            "llm_total_tokens": func.coalesce(TestRun.llm_total_tokens, 0) + usage.total_tokens,
        }
        if usage.provider:
            updates["llm_provider"] = usage.provider
        if usage.model:
            updates["llm_model"] = usage.model

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TestRun).where(TestRun.id == run_id).values(**updates)
            )
            await db.commit()
            result = await db.execute(select(TestRun).where(TestRun.id == run_id))
            run = result.scalar_one_or_none()

        if run:
            usage_message = (
                f"LLM usage for scan {run_id}: batch {usage.total_tokens} tokens "
                f"(input {usage.prompt_tokens}, output {usage.completion_tokens}, "
                f"reasoning {usage.reasoning_tokens}); cumulative {run.llm_total_tokens} tokens "
                f"(input {run.llm_prompt_tokens}, output {run.llm_completion_tokens}, "
                f"reasoning {run.llm_reasoning_tokens}) across {run.llm_call_count} calls; "
                f"provider usage metadata received for {run.llm_usage_reported_calls} calls."
            )
            if run.llm_usage_reported_calls < run.llm_call_count:
                logger.warning(usage_message)
            else:
                logger.info(usage_message)
    except Exception as e:
        # Token accounting is diagnostic and must not turn a successful scan into a failure.
        logger.warning("Could not persist LLM token usage for scan %s: %s", run_id, e, exc_info=True)

async def _looks_like_login_page(page) -> bool:
    """
    A page is treated as a login page whenever it shows a visible password
    input. URL path is deliberately ignored — many SPAs host the login form
    at /, /welcome, or a non-obvious route. A visible password input is the
    authoritative signal that submitting a form here would attempt a sign-in.
    """
    try:
        loc = page.locator('input[type="password"]').first
        if await loc.count() == 0:
            return False
        return bool(await loc.is_visible())
    except Exception:
        return False


async def _perform_authentication(page, credentials: Dict[str, Any]) -> bool:
    """
    Pre-flight login routine using supplied credentials.
    Returns True if URL changed after submit (login likely succeeded).
    """
    login_url = credentials.get("login_url")
    username = credentials.get("username") or ""
    password = credentials.get("password") or ""
    if not (login_url or username or password):
        return False

    try:
        current_url = page.url
        if login_url and login_url.rstrip("/") != current_url.rstrip("/"):
            await page.goto(login_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(1000)

        # Fill username — try progressively broader selectors
        username_selectors = [
            'input[type="email"]',
            'input[name="username"]',
            'input[name="user"]',
            'input[id="username"]',
            'input[name*="email"]',
            'input[id*="email"]',
            'input[id*="user"]',
            'input[type="text"]',  # generic text fallback
        ]
        filled_user = False
        if username:
            for sel in username_selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.fill(username)
                        filled_user = True
                        logger.info("Authentication: filled username with selector %r", sel)
                        break
                except Exception:
                    continue

        # Fill password
        filled_pass = False
        if password:
            try:
                pass_loc = page.locator('input[type="password"]').first
                if await pass_loc.count() > 0:
                    await pass_loc.fill(password)
                    filled_pass = True
            except Exception:
                pass

        if username and not filled_user:
            logger.warning("Authentication: could not locate username field")
        if password and not filled_pass:
            logger.warning("Authentication: could not locate password field")

        before_url = page.url

        # Click submit — try multiple strategies
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("Submit")',
        ]
        clicked = False
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click()
                    clicked = True
                    logger.info("Authentication: clicked submit with selector %r", sel)
                    break
            except Exception:
                continue

        if not clicked:
            logger.warning("Authentication: no submit button found, trying Enter key")
            try:
                await page.keyboard.press("Enter")
                clicked = True
            except Exception:
                pass

        if clicked:
            try:
                await page.wait_for_url(
                    lambda url: url.rstrip("/") != before_url.rstrip("/"),
                    timeout=10000,
                )
                logger.info("Authentication: URL changed to %s", page.url)
            except Exception:
                logger.warning("Authentication: URL did not change after submit (still %s)", page.url)

        # Extra wait for SPA to settle
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        success = page.url.rstrip("/") != before_url.rstrip("/")
        logger.info("Authentication: success=%s final_url=%s", success, page.url)
        return success

    except Exception as auth_err:
        logger.warning("Authentication flow encountered non-fatal error: %s", auth_err)
        return False

async def _discover_and_generate_ai_tests(page, options: Dict[str, Any]) -> List[Any]:
    """Inspect page landmarks and generate structured test cases via LLM."""
    started: Optional[float] = None
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
        started = time.perf_counter()
        raw_response = await client.generate(prompt=prompt, system_prompt=SYSTEM_PROMPT)
        ai_seconds = time.perf_counter() - started
        valid, case_model, error = SchemaEnforcer.parse_test_case(raw_response)
        if valid and case_model:
            logger.info(f"AI test case timing: goal-based test case '{case_model.id}' written in {ai_seconds:.1f}s")
            return [case_model]
        logger.warning(f"AI test case timing: AI answered in {ai_seconds:.1f}s but the test case was invalid: {error}")
    except Exception as ai_err:
        ai_seconds = time.perf_counter() - started if started else 0.0
        logger.warning(f"Autonomous AI test generation skipped after {ai_seconds:.1f}s: {ai_err}")
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
    partial: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Async execution routine inside Playwright worker.

    ``partial`` is filled with each pillar result as soon as it finishes, so the
    caller can still save a report if the overall time limit is reached.
    """
    if partial is None:
        partial = {}
    partial["target_url"] = target_url
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
    headless = bool(profile.get("headless", True))
    slow_mo_ms = int(profile.get("slow_mo_ms", 0 if headless else 300))

    context = None
    page = None
    security_task: Optional[asyncio.Task] = None
    performance_task: Optional[asyncio.Task] = None
    try:
        browser = await BrowserPool.get_browser(
            browser_type=browser_type,
            headless=headless,
            slow_mo=slow_mo_ms,
        )
        mode_desc = "headless" if headless else "live headed desktop view"
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
            allow_private_hosts=bool(profile.get("allow_private_hosts", False)),
        )
        await ScanProgressTracker.log(
            run_id,
            "INITIALIZING",
            f"Created isolated Playwright {browser_type} context ({mode_desc}) with ScopeGuard",
            10,
        )
        if record_trace:
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        await CoreWebVitalsScanner.install_observer(context)
        page = await context.new_page()

        if profile.get("viewport_width") and profile.get("viewport_height"):
            await page.set_viewport_size({"width": profile["viewport_width"], "height": profile["viewport_height"]})

        # Pre-flight authentication if credentials supplied
        authenticated = False
        if creds:
            await ScanProgressTracker.log(run_id, "AUTHENTICATING", f"Executing authentication flow on {creds.get('login_url', target_url)}", 12)
            auth_result = await _perform_authentication(page, creds)
            authenticated = bool(auth_result) and not await _looks_like_login_page(page)

            # Recovery: SPAs often briefly navigate to a protected route after login,
            # then an async auth check fails and bounces back to /login. Detect this
            # and try one re-navigation to the user-supplied target URL.
            if await _looks_like_login_page(page):
                logger.warning(
                    "Authentication: still on login-like page after auth (%s); "
                    "retrying by navigating to %s",
                    page.url, target_url,
                )
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1500)
                except Exception as e:
                    logger.warning("Authentication: recovery navigation failed: %s", e)

                if await _looks_like_login_page(page):
                    logger.warning(
                        "Authentication: still on login page after recovery (%s). "
                        "Credentials may be wrong or the SPA requires extra auth steps — "
                        "scan will only cover pages reachable without login.",
                        page.url,
                    )
                    await ScanProgressTracker.log(
                        run_id,
                        "AUTHENTICATING",
                        "Login did not persist — SPA bounced back to login page. Scan will cover only public pages.",
                        15,
                    )
                else:
                    # Recovery navigation succeeded — session is active.
                    authenticated = True

            # After login the app may redirect to its default landing page (e.g. /home).
            # Navigate back to the requested target module before crawling.
            if page.url.rstrip("/") != target_url.rstrip("/"):
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass

        # Always crawl from the requested target URL, not the post-login landing page.
        # After authentication the app may redirect to /home; we must navigate back to
        # the actual target so the crawler and all pillar scanners see the right module.
        start_crawl_url = target_url
        crawl_depth = int(profile.get("max_crawl_depth", 5))
        # depth=0 means scan only the target page itself (no link following).
        # depth=1 means target + its direct children. Keeps module scans focused.
        crawl_max_pages = 1 if crawl_depth == 0 else int(profile.get("max_pages", 100))
        human_mode = bool(test_pack_yaml)
        use_ai = not human_mode
        page_interaction_results: List[Dict[str, Any]] = []
        page_combinatorial_results: List[Dict[str, Any]] = []
        functional_scanner = FunctionalScanner()
        exhaustive_interactions = bool(profile.get("exhaustive_interactions", True))
        crawl_budget_seconds = max(5, int(profile["timeout_seconds"] * 0.85))
        interaction_deadline = time.monotonic() + crawl_budget_seconds
        per_page_time_budget = int(profile.get("per_page_time_budget_seconds") or 0)

        async def explore_page_before_following_links(browser_page, crawled_page):
            """Complete this page's safe interaction pass before the crawler follows its links."""
            if not (profile["check_functional"] and use_ai):
                return True
            if await _is_cancelled(run_id):
                raise asyncio.CancelledError("Scan cancelled during page interaction exploration")
            page_url = crawled_page.final_url or crawled_page.url
            if not bool((crawled_page.page_observations or {}).get("interactive_ready", False)):
                blocked = {
                    "mode": (
                        "pairwise"
                        if profile.get("interaction_coverage_mode") == "pairwise"
                        else "exhaustive_cartesian"
                    ) if exhaustive_interactions else "ai_exploratory",
                    "page_url": redact_url(page_url),
                    "factors_discovered": 0,
                    "planned_cases": 0,
                    "executed_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                    "blocked_cases": 0,
                    "pending_cases": 0,
                    "case_limit": int(profile.get("max_generated_cases") or 1000),
                    "limit_exceeded": False,
                    "action_order_permutations": bool(profile.get("include_action_order_permutations", False)),
                    "complete": False,
                    "interruption_reason": "The page did not reach an interactive ready state; no controls were tested.",
                    "test_cases": [],
                }
                if exhaustive_interactions:
                    page_combinatorial_results.append(blocked)
                else:
                    page_interaction_results.append({
                        "actions": 0, "completed": False, "counted": False, "passed": False,
                        "findings": [], "history": [], "visual_paths": [], "page_url": redact_url(page_url),
                    })
                return False
            # When we have a valid authenticated session, do not run interaction
            # tests on login pages — the matrix would fill the form with dummy
            # values and submit, which the backend rejects and often rotates the
            # session cookie, logging us out for the rest of the scan.
            if authenticated and await _looks_like_login_page(browser_page):
                skipped_record = {
                    "mode": (
                        "pairwise"
                        if profile.get("interaction_coverage_mode") == "pairwise"
                        else "exhaustive_cartesian"
                    ) if exhaustive_interactions else "ai_exploratory",
                    "page_url": redact_url(page_url),
                    "factors_discovered": 0,
                    "planned_cases": 0,
                    "executed_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                    "blocked_cases": 0,
                    "pending_cases": 0,
                    "case_limit": int(profile.get("max_generated_cases") or 1000),
                    "limit_exceeded": False,
                    "action_order_permutations": bool(profile.get("include_action_order_permutations", False)),
                    "complete": True,
                    "interruption_reason": "Login form skipped to preserve the authenticated session — submitting dummy credentials would log the crawler out.",
                    "test_cases": [],
                }
                if exhaustive_interactions:
                    page_combinatorial_results.append(skipped_record)
                else:
                    page_interaction_results.append({
                        "actions": 0, "completed": True, "counted": False, "passed": True,
                        "findings": [], "history": [], "visual_paths": [],
                        "page_url": redact_url(page_url),
                    })
                await ScanProgressTracker.log(
                    run_id,
                    "FUNCTIONAL",
                    f"Skipped login-form interactions on {redact_url(page_url)} to preserve the authenticated session.",
                    20,
                )
                return True

            await ScanProgressTracker.log(
                run_id,
                "FUNCTIONAL",
                f"Testing interactive controls on {redact_url(page_url)} before following linked pages...",
                18,
            )
            if exhaustive_interactions:
                matrix_resume_state = await _load_matrix_checkpoint(run_id, page_url)
                matrix = await functional_scanner.test_crawled_page_combinations(
                    page=browser_page,
                    target_url=page_url,
                    run_id=run_id,
                    text_values=list(profile.get("interaction_test_values") or []),
                    max_generated_cases=int(profile.get("max_generated_cases") or 1000),
                    include_action_order_permutations=bool(profile.get("include_action_order_permutations", False)),
                    interaction_priority_order=list(profile.get("interaction_priority_order") or [
                        "dropdown", "search", "navigation", "button"
                    ]),
                    interaction_coverage_mode=str(profile.get("interaction_coverage_mode") or "exhaustive"),
                    interaction_categories=list(profile.get("interaction_categories") or [
                        "dropdown", "search", "navigation", "button", "form"
                    ]),
                    interaction_required_combinations=list(
                        profile.get("interaction_required_combinations") or []
                    ),
                    resume_state=matrix_resume_state,
                    checkpoint_callback=lambda checkpoint: _save_matrix_checkpoint(run_id, page_url, checkpoint),
                    deadline=(
                        min(interaction_deadline, time.monotonic() + per_page_time_budget)
                        if per_page_time_budget > 0
                        else interaction_deadline
                    ),
                    cancellation_check=lambda: _is_cancelled(run_id),
                )
                matrix["page_url"] = redact_url(page_url)
                linked_routes = matrix.pop("linked_routes", [])
                matrix["linked_routes_discovered"] = [redact_url(route) for route in linked_routes]
                if matrix.get("complete"):
                    for route in linked_routes:
                        if route not in crawled_page.links:
                            crawled_page.links.append(route)
                page_combinatorial_results.append(matrix)
                await ScanProgressTracker.log(
                    run_id,
                    "FUNCTIONAL",
                    f"Page matrix finished: {matrix.get('executed_cases', 0)}/{matrix.get('planned_cases', 0)} cases executed; "
                    f"{matrix.get('pending_cases', 0)} pending.",
                    30,
                    level="WARNING" if not matrix.get("complete") else "INFO",
                )
                # Do not traverse linked routes until this page's full manifest is complete.
                return bool(matrix.get("complete"))

            journey = await functional_scanner.explore_crawled_page(
                page=browser_page,
                target_url=page_url,
                run_id=run_id,
                initial_status_code=crawled_page.status_code,
                page_ready=bool((crawled_page.page_observations or {}).get("interactive_ready", False)),
                ai_test_goals=profile.get("ai_test_goals") or opts.get("ai_test_goals") or "",
                max_react_actions=int(profile.get("max_react_actions") or opts.get("max_react_actions") or 25),
                cancellation_check=lambda: _is_cancelled(run_id),
            )
            journey["page_url"] = redact_url(page_url)
            page_interaction_results.append(journey)
            return bool(journey.get("completed"))

        await ScanProgressTracker.log(run_id, "CRAWLING", "Crawling in-scope routes and analyzing site structure...", 15)
        crawl_result = await SiteCrawler.crawl(
            page,
            start_crawl_url,
            set(allowed_domains),
            max_pages=crawl_max_pages,
            max_depth=crawl_depth,
            # Reserve the final 15% for pillars, report persistence, and artifacts.
            timeout_seconds=crawl_budget_seconds,
            request_rate_limit=int(profile["rate_limit"]),
            include_sitemap_urls=bool(profile.get("include_sitemap_urls", False)),
            traversal_strategy=str(profile.get("traversal_strategy", "bfs")),
            explore_button_navigations=bool(profile.get("explore_button_navigations", False)),
            allow_state_changing=bool(profile.get("allow_state_changing", False)),
            continue_on_incomplete_coverage=bool(profile.get("continue_on_incomplete_coverage", True)),
            page_callback=explore_page_before_following_links,
            cancellation_check=lambda: _is_cancelled(run_id),
        )

        # Check cancellation before initial run
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled before start")

        if exhaustive_interactions and use_ai and _matrix_batch_can_resume(page_combinatorial_results):
            total_executed = sum(int(item.get("executed_cases") or 0) for item in page_combinatorial_results)
            total_planned = sum(int(item.get("planned_cases") or 0) for item in page_combinatorial_results)
            continuation_note = (
                f"Interaction batch finished ({total_executed}/{total_planned} planned cases so far). "
                "The scan will resume from its saved page cursors."
            )
            await _mark_scan_continuing(run_id, continuation_note)
            return {"status": "RESUME_REQUIRED", "run_id": run_id}

        # Two exclusive modes: a human-written test pack means NO AI anywhere in the scan
        # (no AI test writing, failure explanations, accessibility fixes, or screenshot review).
        partial["test_case_mode"] = "human" if human_mode else "ai"

        # 1. Functional Pillar (the authored test pack runs against its starting page).
        test_cases = []
        if human_mode:
            valid, pack, errors = SchemaValidator.validate_yaml_pack(test_pack_yaml)
            if not valid or not pack:
                raise ValueError(f"Test pack failed worker-side validation: {errors}")
            test_cases = pack.cases
            await ScanProgressTracker.log(
                run_id, "FUNCTIONAL",
                f"Human test pack mode: running {len(test_cases)} human-written test case(s); AI is turned off for this scan",
                22,
            )
        elif not exhaustive_interactions and profile.get("use_ai_generation", True) and profile.get("ai_test_goals"):
            await ScanProgressTracker.log(run_id, "FUNCTIONAL", "Synthesizing AI test scenarios for target goals...", 22)
            if page.url.rstrip("/") != target_url.rstrip("/"):
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await wait_for_page_ready(page)
            ai_cases = await _discover_and_generate_ai_tests(page, profile)
            test_cases.extend(ai_cases)

        # Kick off Security and Performance as background tasks: neither touches
        # the live Playwright page (both read from the already-completed crawl
        # result, and their external work -- TLS sockets, Nuclei subprocess,
        # Lighthouse subprocess -- runs entirely outside the browser). Running
        # them alongside Functional and Accessibility, which share the page,
        # saves 2-5 minutes per scan on real targets without adding contention.
        async def _background_security() -> PillarScanResult:
            if not profile["check_security"]:
                await ScanProgressTracker.log(run_id, "SECURITY", "Security pillar skipped (disabled in scan profile)", 68)
                return PillarScanResult(
                    pillar=PillarType.SECURITY,
                    score=100.0,
                    status="SKIPPED",
                    metrics={"skipped": True, "reason": "disabled by scan profile"},
                )
            await ScanProgressTracker.log(run_id, "SECURITY", "Starting Security Pillar (background): Auditing headers, cookies, TLS & exposures...", 50)
            try:
                result = await SecurityScanner().scan(
                    page,
                    target_url,
                    crawl_result=crawl_result,
                    request_rate_limit=int(profile["rate_limit"]),
                )
                await ScanProgressTracker.log(run_id, "SECURITY", f"Security pillar completed (Score: {result.score}/100, Findings: {len(result.findings)})", 68)
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background task must not kill the scan
                logger.exception("Security pillar failed in background: %s", exc)
                return PillarScanResult(
                    pillar=PillarType.SECURITY,
                    score=0.0,
                    status="FAILED",
                    metrics={"error": f"{type(exc).__name__}: {exc}"},
                )

        async def _background_performance() -> PillarScanResult:
            if not profile["check_performance"]:
                await ScanProgressTracker.log(run_id, "PERFORMANCE", "Performance pillar skipped (disabled in scan profile)", 82)
                return PillarScanResult(
                    pillar=PillarType.PERFORMANCE,
                    score=100.0,
                    status="SKIPPED",
                    metrics={"skipped": True, "reason": "disabled by scan profile"},
                )
            await ScanProgressTracker.log(run_id, "PERFORMANCE", "Starting Performance Pillar (background): Measuring navigation timings and Core Web Vitals...", 70)
            try:
                result = await PerformanceScanner().scan(page, target_url, crawl_result=crawl_result)
                await ScanProgressTracker.log(run_id, "PERFORMANCE", f"Performance pillar completed (Score: {result.score}/100, Findings: {len(result.findings)})", 82)
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background task must not kill the scan
                logger.exception("Performance pillar failed in background: %s", exc)
                return PillarScanResult(
                    pillar=PillarType.PERFORMANCE,
                    score=0.0,
                    status="FAILED",
                    metrics={"error": f"{type(exc).__name__}: {exc}"},
                )

        security_task = asyncio.create_task(_background_security())
        performance_task = asyncio.create_task(_background_performance())

        if profile["check_functional"]:
            cases_label = (
                f"{len(test_cases)} authored test cases" if test_cases else
                "exhaustive page interaction matrix" if exhaustive_interactions and use_ai else
                "autonomous form discovery & AI interaction exploration"
            )
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
            func_result = await functional_scanner.scan(
                page,
                target_url,
                test_cases=test_cases,
                run_id=run_id,
                allow_state_changing=bool(profile["allow_state_changing"]),
                use_ai=use_ai,
                crawl_result=crawl_result,
                initial_status_code=response.status if response else None,
                ai_test_goals=profile.get("ai_test_goals") or opts.get("ai_test_goals") or "",
                max_react_actions=int(profile.get("max_react_actions") or opts.get("max_react_actions") or 25),
                precomputed_ai_journeys=page_interaction_results,
                precomputed_combinatorial_results=page_combinatorial_results,
                exhaustive_interactions=exhaustive_interactions and use_ai,
                interaction_coverage_mode=str(profile.get("interaction_coverage_mode") or "exhaustive"),
                skip_ai_journey=bool(use_ai),
            )
            if use_ai and profile.get("ai_explain_failures", True) and func_result.findings:
                await ScanProgressTracker.log(run_id, "FUNCTIONAL", "Diagnosing functional failures with AI...", 40)
                await _enrich_findings_with_ai(func_result.findings)
            await ScanProgressTracker.log(run_id, "FUNCTIONAL", f"Functional pillar completed (Score: {func_result.score}/100, Findings: {len(func_result.findings)})", 48)
        else:
            await ScanProgressTracker.log(run_id, "FUNCTIONAL", "Functional pillar skipped (disabled in scan profile)", 48)
            func_result = PillarScanResult(
                pillar=PillarType.FUNCTIONAL,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        partial["target_url"] = target_url
        partial[PillarType.FUNCTIONAL] = func_result

        if await _is_cancelled(run_id):
            security_task.cancel()
            performance_task.cancel()
            raise asyncio.CancelledError("Scan cancelled after functional pillar")

        # 2 & 3. Collect Security and Performance results that ran in parallel
        # with the Functional pillar. Each background task catches its own
        # exceptions and returns a FAILED PillarScanResult on error, so neither
        # can bring down the scan here.
        sec_result, perf_result = await asyncio.gather(security_task, performance_task)
        partial[PillarType.SECURITY] = sec_result
        partial[PillarType.PERFORMANCE] = perf_result

        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after performance pillar")

        # 4. Accessibility & UX Pillar (Axe-core WCAG + optional text fixes + responsive checks)
        if profile["check_accessibility"]:
            await ScanProgressTracker.log(run_id, "ACCESSIBILITY", "Starting Accessibility Pillar: Running Axe-core WCAG rules, text-based LLM fixes & responsive audits...", 85)
            if page.is_closed():
                page = await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            a11y_result = await AccessibilityScanner().scan(
                page,
                target_url,
                run_id=run_id,
                comparison_paths=func_result.metrics.get("visual_comparison_paths", []),
                use_ai=use_ai,
                use_visual_ai=False,
                crawl_result=crawl_result,
            )
            await ScanProgressTracker.log(run_id, "ACCESSIBILITY", f"Accessibility pillar completed (Score: {a11y_result.score}/100, Findings: {len(a11y_result.findings)})", 95)
        else:
            await ScanProgressTracker.log(run_id, "ACCESSIBILITY", "Accessibility pillar skipped (disabled in scan profile)", 95)
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
        report_dict["test_cases"] = func_result.metrics.get("executed_test_cases", [])
        report_dict["test_case_mode"] = "human" if human_mode else "ai"
        report_dict["ai_used"] = use_ai
        coverage_pending = int(func_result.metrics.get("pending_combinatorial_cases") or 0)
        coverage_complete = bool(func_result.metrics.get("combinatorial_coverage_complete", True))
        coverage_note = None
        if exhaustive_interactions and use_ai and not coverage_complete:
            coverage_pages = func_result.metrics.get("combinatorial_pages") or []
            coverage_reasons = [
                str(item.get("interruption_reason"))
                for item in coverage_pages
                if item.get("interruption_reason")
            ]
            reason_text = " ".join(coverage_reasons[:3]) or "No complete page matrix was recorded."
            coverage_note = (
                f"Coverage incomplete: {coverage_pending} interaction cases remain pending. {reason_text} "
                "Review functional coverage details; choose pairwise coverage, fewer control categories or values, "
                "or a longer scan timeout before rerunning."
            )
            report_dict["incomplete"] = True
            report_dict["incomplete_reason"] = coverage_note
            report_dict["passed"] = False
        await _persist_scan_success(run_id, report_dict, artifacts=artifacts, note=coverage_note)
        if coverage_complete or not _matrix_batch_can_resume(func_result.metrics.get("combinatorial_pages") or []):
            await _clear_matrix_checkpoints(run_id)
        await ScanProgressTracker.log(
            run_id, "COMPLETED",
            coverage_note or f"Audit finished successfully! Overall Score: {unified_report.overall_score}/100, Grade: {unified_report.grade}",
            100,
            level="WARNING" if coverage_note else "INFO",
        )
        return report_dict

    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if current_task and current_task.cancelling():
            raise
        logger.info(f"Scan run {run_id} aborted due to cancellation.")
        await _persist_scan_failure(run_id, "Scan cancelled by user request", is_cancelled=True)
        await _clear_matrix_checkpoints(run_id)
        return {"status": "CANCELLED", "run_id": run_id}
    except Exception as exc:
        await _persist_scan_failure(run_id, str(exc), is_cancelled=False)
        raise
    finally:
        for pending_task in (security_task, performance_task):
            if pending_task is not None and not pending_task.done():
                pending_task.cancel()
                try:
                    await pending_task
                except (asyncio.CancelledError, Exception):
                    pass
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
        try:
            # The Celery entrypoint creates a fresh asyncio loop per task. Stop
            # Playwright here so BrowserPool never carries loop-bound objects into
            # the next continuation task handled by this worker process.
            await BrowserPool.close()
        except Exception as browser_close_err:
            logger.warning("Could not close Playwright for scan %s: %s", run_id, browser_close_err)
        if temp_video_dir and os.path.exists(temp_video_dir):
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
    partial: Dict[str, Any] = {}
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
                partial=partial,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        err_msg = f"Scan exceeded its {timeout_seconds}-second timeout limit"
        if any(pillar in partial for pillar in PillarType):
            try:
                return await _persist_partial_report(run_id, partial, timeout_seconds)
            except Exception as e:
                logger.error(f"Could not save partial report for {run_id}: {e}", exc_info=True)
        logger.error(f"Scan {run_id} failed: {err_msg}")
        try:
            await _persist_scan_failure(run_id, err_msg)
            await ScanProgressTracker.log(run_id, "FAILED", err_msg, 100, level="ERROR")
        except Exception as e:
            logger.error(f"Error persisting scan failure for {run_id}: {e}")
        return {"status": "FAILED", "run_id": run_id, "error": "scan_timeout"}


async def _execute_and_record_usage(
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
    """Run one scan batch and persist all Gemini/Ollama usage from its calls."""
    from ai_engine.clients.usage import TokenUsage, begin_token_usage, end_token_usage

    usage = TokenUsage()
    context_token = begin_token_usage(usage)
    try:
        return await _execute_with_timeout(
            run_id=run_id,
            target_url=target_url,
            allowed_domains=allowed_domains,
            test_pack_yaml=test_pack_yaml,
            cookies=cookies,
            credentials=credentials,
            options=options,
            scan_profile=scan_profile,
            timeout_seconds=timeout_seconds,
        )
    finally:
        end_token_usage(context_token)
        await _persist_llm_usage(run_id, usage)


def _persist_worker_failure(run_id: str, message: str) -> None:
    async def persist_in_fresh_loop():
        await _persist_scan_failure(run_id, message)

    asyncio.run(persist_in_fresh_loop())


async def _persist_cancelled_and_clear_checkpoints(run_id: str) -> None:
    await _persist_scan_failure(run_id, "Scan cancelled by user request", is_cancelled=True)
    await _clear_matrix_checkpoints(run_id)

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
    continuation_task_id: Optional[str] = None,
    **kwargs: Any,
):
    """Celery task entrypoint."""
    if continuation_task_id and continuation_task_id != self.request.id:
        logger.info("Ignoring stale interaction continuation task %s", self.request.id)
        return {"status": "DUPLICATE_CONTINUATION_IGNORED", "run_id": run_id}
    if continuation_task_id:
        try:
            import redis
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            current_lock = client.get(f"matrix_resume:{run_id}:continuation")
            client.close()
            current_lock = current_lock.decode() if isinstance(current_lock, bytes) else current_lock
            if current_lock != continuation_task_id:
                logger.info("Ignoring superseded interaction continuation %s", self.request.id)
                return {"status": "DUPLICATE_CONTINUATION_IGNORED", "run_id": run_id}
        except Exception:
            logger.exception("Could not validate interaction continuation for run %s", run_id)
            _persist_worker_failure(run_id, "Could not validate the interaction continuation")
            return {"status": "FAILED", "run_id": run_id, "error": "continuation_validation_failed"}
    else:
        try:
            import redis
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            current_lock = client.get(f"matrix_resume:{run_id}:continuation")
            client.close()
            if current_lock:
                logger.info("Ignoring duplicate root task while run %s already has a continuation", run_id)
                return {"status": "CONTINUING", "run_id": run_id}
        except Exception:
            # The matrix checkpoint path will report Redis failures explicitly.
            pass
    logger.info(f"Starting automated scan task for run {run_id} on {redact_url(target_url)}")

    if asyncio.run(_is_cancelled(run_id)):
        asyncio.run(_persist_cancelled_and_clear_checkpoints(run_id))
        return {"status": "CANCELLED", "run_id": run_id}

    effective_profile = {**(options or {}), **(scan_profile or {})}
    # Allow at least 300s (5 minutes) so that crawler + multi-step AI ReAct journey can complete
    raw_timeout = int(effective_profile.get("timeout_seconds") or 300)
    timeout_seconds = max(300, raw_timeout)
    result = asyncio.run(_execute_and_record_usage(
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
    if result.get("status") == "RESUME_REQUIRED":
        continuation_id = str(uuid.uuid4())
        continuation_key = f"matrix_resume:{run_id}:continuation"
        client = None
        try:
            import redis
            client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            expected_lock = self.request.id if continuation_task_id else "__no_lock__"
            reserve_script = """
                local current = redis.call('GET', KEYS[1])
                if ARGV[1] == '__no_lock__' then
                    if current then return 0 end
                elseif current ~= ARGV[1] then
                    return 0
                end
                redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
                return 1
            """
            reserved = client.eval(
                reserve_script, 1, continuation_key, expected_lock,
                continuation_id, 30 * 24 * 60 * 60,
            )
            if not reserved:
                client.close()
                logger.info("Another continuation is already queued for run %s", run_id)
                return {"status": "CONTINUING", "run_id": run_id}
        except Exception:
            logger.exception("Could not reserve an interaction continuation for run %s", run_id)
            if client:
                client.close()
            _persist_worker_failure(run_id, "Could not queue the next interaction matrix batch")
            return {"status": "FAILED", "run_id": run_id, "error": "continuation_queue_failed"}

        try:
            self.apply_async(
                task_id=continuation_id,
                kwargs={
                    "run_id": run_id,
                    "target_url": target_url,
                    "allowed_domains": allowed_domains,
                    "test_pack_yaml": test_pack_yaml,
                    "cookies": cookies,
                    "credentials": credentials,
                    "options": options,
                    "scan_profile": scan_profile,
                    "continuation_task_id": continuation_id,
                },
                countdown=1,
            )
        except Exception:
            try:
                client.delete(continuation_key)
            finally:
                client.close()
            logger.exception("Could not enqueue interaction continuation for run %s", run_id)
            _persist_worker_failure(run_id, "Could not queue the next interaction matrix batch")
            return {"status": "FAILED", "run_id": run_id, "error": "continuation_queue_failed"}
        client.close()
        logger.info(
            "Queued interaction-matrix continuation for run %s as task %s",
            run_id,
            continuation_id,
        )
        return {"status": "CONTINUING", "run_id": run_id, "continuation_task_id": continuation_id}
    return result
