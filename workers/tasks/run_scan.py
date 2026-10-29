import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from workers.celery_app import celery_app
from test_engine.browser.pool import BrowserPool
from test_engine.browser.context import ContextFactory
from test_engine.pillars.functional.runner import FunctionalScanner
from test_engine.crawl import CrawlResult, SiteCrawler, redact_url
from test_engine.pillars.security.runner import SecurityScanner
from test_engine.pillars.performance.runner import PerformanceScanner
from test_engine.pillars.performance.vitals import CoreWebVitalsScanner
from test_engine.pillars.accessibility.runner import AccessibilityScanner
from test_engine.pillars.accessibility.axe_runner import AxeCoreScanner
from test_engine.pillars.accessibility.responsive import ResponsiveViewportScanner
from test_engine.pillars.base import PillarScanResult
from test_engine.scoring.aggregator import ReportAggregator
from test_engine.schema.validator import SchemaValidator
from app.common.constants import PillarType, RunStatus
from app.core.config import settings

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

async def _persist_scan_success(run_id: str, report_data: Dict[str, Any]) -> None:
    """Save report summary and mark test run as completed."""
    try:
        from app.core.database import AsyncSessionLocal
        from app.apps.test_runs.models import TestRun
        from app.apps.reports.services import save_report
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            await save_report(db, run_id, report_data)
            stmt = select(TestRun).where(TestRun.id == run_id)
            result = await db.execute(stmt)
            run = result.scalar_one_or_none()
            if run:
                run.status = RunStatus.COMPLETED
                run.finished_at = datetime.now(timezone.utc)
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

async def _async_execute_scan(
    run_id: str,
    target_url: str,
    allowed_domains: List[str],
    test_pack_yaml: Optional[str] = None,
    cookies: Optional[List[Dict[str, Any]]] = None,
    scan_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Async execution routine inside Playwright worker."""
    await _persist_scan_start(run_id)
    profile = {
        "check_functional": True,
        "check_security": True,
        "check_performance": True,
        "check_accessibility": True,
        "allow_state_changing": False,
        "rate_limit": settings.DEFAULT_SCAN_RATE_LIMIT,
        "timeout_seconds": 900,
        **(scan_profile or {}),
    }
    context = None
    try:
        browser = await BrowserPool.get_browser()
        context = await ContextFactory.create_isolated_context(
            browser=browser,
            allowed_domains=set(allowed_domains),
            cookies=cookies,
            request_rate_limit=int(profile["rate_limit"]),
            target_url=target_url,
        )
        await CoreWebVitalsScanner.install_observer(context)
        page = await context.new_page()

        # Discover and visit safe, in-scope links before running site-wide audits.
        crawl_result = await SiteCrawler.crawl(
            page,
            target_url,
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

        if profile["check_functional"]:
            response = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            func_result = await FunctionalScanner().scan(
                page,
                target_url,
                test_cases=test_cases,
                run_id=run_id,
                allow_state_changing=bool(profile["allow_state_changing"]),
                crawl_result=crawl_result,
                initial_status_code=response.status if response else None,
            )
        else:
            func_result = PillarScanResult(
                pillar=PillarType.FUNCTIONAL,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        # Check cancellation before Security Pillar
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after functional pillar")

        # 2. Security Pillar, using observations from every crawled page.
        if profile["check_security"]:
            sec_result = await SecurityScanner().scan(
                page,
                target_url,
                crawl_result=crawl_result,
                request_rate_limit=int(profile["rate_limit"]),
            )
        else:
            sec_result = PillarScanResult(
                pillar=PillarType.SECURITY,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        # Check cancellation before Performance Pillar
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after security pillar")

        # 3. Performance Pillar, with page-level metrics plus site p75 summaries.
        if profile["check_performance"]:
            perf_result = await PerformanceScanner().scan(page, target_url, crawl_result=crawl_result)
        else:
            perf_result = PillarScanResult(
                pillar=PillarType.PERFORMANCE,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        # Check cancellation before Accessibility Pillar
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after performance pillar")

        # 4. Accessibility & UX Pillar (Axe-core WCAG + AI Code Fixes + Mobile + Multimodal Vision)
        if profile["check_accessibility"]:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            a11y_result = await AccessibilityScanner().scan(
                page,
                target_url,
                run_id=run_id,
                comparison_paths=func_result.metrics.get("visual_comparison_paths", []),
                crawl_result=crawl_result,
            )
        else:
            a11y_result = PillarScanResult(
                pillar=PillarType.ACCESSIBILITY,
                score=100.0,
                status="SKIPPED",
                metrics={"skipped": True, "reason": "disabled by scan profile"},
            )

        # Aggregate 4 Pillars
        unified_report = ReportAggregator.aggregate(
            run_id=run_id,
            target_url=target_url,
            pillar_results=[func_result, sec_result, perf_result, a11y_result]
        )

        report_dict = unified_report.model_dump()
        await _persist_scan_success(run_id, report_dict)
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
        if context:
            await context.close()

@celery_app.task(bind=True, name="workers.tasks.run_scan.execute_website_scan")
def execute_website_scan(
    self,
    run_id: str,
    target_url: str,
    allowed_domains: List[str],
    test_pack_yaml: Optional[str] = None,
    cookies: Optional[List[Dict[str, Any]]] = None,
    scan_profile: Optional[Dict[str, Any]] = None,
):
    """Celery task entrypoint."""
    logger.info(f"Starting automated scan task for run {run_id} on {redact_url(target_url)}")
    timeout_seconds = int((scan_profile or {}).get("timeout_seconds", 900))
    try:
        return asyncio.run(asyncio.wait_for(_async_execute_scan(
            run_id=run_id,
            target_url=target_url,
            allowed_domains=allowed_domains,
            test_pack_yaml=test_pack_yaml,
            cookies=cookies,
            scan_profile=scan_profile,
        ), timeout=timeout_seconds))
    except asyncio.TimeoutError:
        asyncio.run(_persist_scan_failure(run_id, f"Scan exceeded its {timeout_seconds}-second profile timeout"))
        return {"status": "FAILED", "run_id": run_id, "error": "scan_timeout"}
