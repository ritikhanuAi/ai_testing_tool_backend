import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from workers.celery_app import celery_app
from test_engine.browser.pool import BrowserPool
from test_engine.browser.context import ContextFactory
from test_engine.pillars.functional.runner import FunctionalScanner
from test_engine.pillars.security.headers import SecurityHeadersScanner
from test_engine.pillars.security.cookies import CookieSecurityScanner
from test_engine.pillars.security.exposures import SensitiveExposureScanner
from test_engine.pillars.security.tls import TLSScanner
from test_engine.pillars.performance.timing import NavigationTimingScanner
from test_engine.pillars.performance.vitals import CoreWebVitalsScanner
from test_engine.pillars.performance.resources import ResourceAuditScanner
from test_engine.pillars.accessibility.runner import AccessibilityScanner
from test_engine.pillars.accessibility.axe_runner import AxeCoreScanner
from test_engine.pillars.accessibility.responsive import ResponsiveViewportScanner
from test_engine.pillars.base import PillarScanResult
from test_engine.scoring.aggregator import ReportAggregator
from test_engine.schema.validator import SchemaValidator
from app.common.constants import PillarType, RunStatus

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
    cookies: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Async execution routine inside Playwright worker."""
    await _persist_scan_start(run_id)

    browser = await BrowserPool.get_browser()
    context = await ContextFactory.create_isolated_context(
        browser=browser,
        allowed_domains=set(allowed_domains),
        cookies=cookies
    )
    page = await context.new_page()

    try:
        # Initial navigation to target site
        response = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        headers = dict(response.headers) if response else {}

        # Check cancellation before initial run
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled before start")

        # 1. Functional Pillar
        test_cases = []
        if test_pack_yaml:
            valid, pack, _ = SchemaValidator.validate_yaml_pack(test_pack_yaml)
            if valid and pack:
                test_cases = pack.cases

        func_scanner = FunctionalScanner()
        func_result = await func_scanner.scan(page, target_url, test_cases=test_cases, run_id=run_id)

        # Check cancellation before Security Pillar
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after functional pillar")

        # 2. Security Pillar
        sec_findings = []
        sec_hdr_findings, sec_hdr_score = SecurityHeadersScanner.audit_headers(headers, target_url)
        sec_findings.extend(sec_hdr_findings)

        browser_cookies = await context.cookies()
        sec_cookie_findings, sec_cookie_score = CookieSecurityScanner.audit_cookies(browser_cookies, target_url)
        sec_findings.extend(sec_cookie_findings)

        sec_exposure_findings, sec_exp_score = await SensitiveExposureScanner.check_exposures(target_url)
        sec_findings.extend(sec_exposure_findings)

        tls_findings, tls_score, tls_metrics = TLSScanner.audit_tls(target_url)
        sec_findings.extend(tls_findings)

        sec_score = round((sec_hdr_score + sec_cookie_score + sec_exp_score + tls_score) / 4.0, 1)
        sec_result = PillarScanResult(
            pillar=PillarType.SECURITY,
            score=sec_score,
            status="PASSED" if sec_score >= 80 else "WARNING",
            findings=sec_findings,
            metrics={"headers_checked": len(headers), "cookies_checked": len(browser_cookies), **tls_metrics}
        )

        # Check cancellation before Performance Pillar
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after security pillar")

        # 3. Performance Pillar
        perf_metrics, timing_findings = await NavigationTimingScanner.extract_timing(page)
        vitals_metrics, vitals_findings = await CoreWebVitalsScanner.capture_vitals(page)
        res_metrics, res_findings = await ResourceAuditScanner.audit_resources(page)

        perf_findings = timing_findings + vitals_findings + res_findings
        perf_score = max(0.0, 100.0 - (len(perf_findings) * 10.0))
        perf_result = PillarScanResult(
            pillar=PillarType.PERFORMANCE,
            score=perf_score,
            status="PASSED" if perf_score >= 75 else "WARNING",
            findings=perf_findings,
            metrics={**perf_metrics, **vitals_metrics, **res_metrics}
        )

        # Check cancellation before Accessibility Pillar
        if await _is_cancelled(run_id):
            raise asyncio.CancelledError("Scan cancelled after performance pillar")

        # 4. Accessibility & UX Pillar (Axe-core WCAG + AI Code Fixes + Mobile + Multimodal Vision)
        a11y_scanner = AccessibilityScanner()
        a11y_result = await a11y_scanner.scan(page, target_url, run_id=run_id)

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
        logger.info(f"Scan run {run_id} aborted due to cancellation.")
        await _persist_scan_failure(run_id, "Scan cancelled by user request", is_cancelled=True)
        return {"status": "CANCELLED", "run_id": run_id}
    except Exception as exc:
        await _persist_scan_failure(run_id, str(exc), is_cancelled=False)
        raise
    finally:
        await context.close()

@celery_app.task(bind=True, name="workers.tasks.run_scan.execute_website_scan")
def execute_website_scan(
    self,
    run_id: str,
    target_url: str,
    allowed_domains: List[str],
    test_pack_yaml: Optional[str] = None,
    cookies: Optional[List[Dict[str, Any]]] = None
):
    """Celery task entrypoint."""
    logger.info(f"Starting automated scan task for run {run_id} on {target_url}")
    return asyncio.run(_async_execute_scan(
        run_id=run_id,
        target_url=target_url,
        allowed_domains=allowed_domains,
        test_pack_yaml=test_pack_yaml,
        cookies=cookies
    ))
