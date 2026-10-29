import time
from typing import List, Dict, Any, Optional
from playwright.async_api import Page
from app.common.constants import FindingSeverity, PillarType
from test_engine.pillars.base import BasePillarScanner, PillarScanResult, PillarFinding
from test_engine.pillars.accessibility.axe_runner import AxeCoreScanner
from test_engine.pillars.accessibility.responsive import ResponsiveViewportScanner
from test_engine.pillars.accessibility.readability import ReadabilityScanner
from test_engine.pillars.accessibility.ux_vision_scanner import UXVisionScanner
from test_engine.crawl.site_crawler import CrawlResult, redact_url

class AccessibilityScanner(BasePillarScanner):
    """
    Comprehensive UI/UX & Accessibility Pillar:
    Unites automated WCAG 2.1 AA audits (axe-core) with AI generative code fixes,
    responsive viewport testing, and multimodal VLM visual design review.
    """

    @property
    def pillar_type(self) -> PillarType:
        return PillarType.ACCESSIBILITY

    async def scan(
        self,
        page: Page,
        target_url: str,
        run_id: str = "manual_run",
        comparison_paths: Optional[List[str]] = None,
        crawl_result: Optional[CrawlResult] = None,
        **kwargs
    ) -> PillarScanResult:
        start_time = time.time()
        findings: List[PillarFinding] = []

        audit_pages = [
            item for item in (crawl_result.pages if crawl_result else [])
            if item.navigation_error is None
        ]
        if not audit_pages:
            audit_urls = [target_url]
        else:
            audit_urls = [item.final_url or item.url for item in audit_pages]

        axe_scores: List[float] = []
        mobile_scores: List[float] = []
        read_scores: List[float] = []
        axe_metrics_by_page: List[Dict[str, Any]] = []
        accessibility_page_errors: List[str] = []
        for index, audit_url in enumerate(audit_urls):
            try:
                if page.url != audit_url:
                    await page.goto(audit_url, wait_until="domcontentloaded", timeout=30000)
                axe_findings, axe_score, axe_metrics = await AxeCoreScanner.run_axe(
                    page,
                    page_url=redact_url(audit_url),
                    generate_fixes=(index == 0),
                )
                findings.extend(axe_findings)
                if axe_metrics.get("axe_audit_completed"):
                    axe_scores.append(axe_score)
                axe_metrics_by_page.append(axe_metrics)

                mobile_findings, mobile_score = await ResponsiveViewportScanner.check_mobile_overflow(page)
                findings.extend(item.model_copy(update={"selector_or_url": redact_url(audit_url)}) for item in mobile_findings)
                mobile_scores.append(mobile_score)
                await page.set_viewport_size({"width": 1280, "height": 720})

                read_findings, read_score = await ReadabilityScanner.audit_elements(page)
                findings.extend(item.model_copy(update={
                    "selector_or_url": f"{redact_url(audit_url)} :: {item.selector_or_url}" if item.selector_or_url else redact_url(audit_url)
                }) for item in read_findings)
                read_scores.append(read_score)
            except Exception as error:
                accessibility_page_errors.append(f"{redact_url(audit_url)}: {type(error).__name__}")

        pages_audited = len(axe_scores)
        if not audit_urls or not axe_scores:
            findings.append(PillarFinding(
                id="A11Y-NO-WCAG-PAGES-AUDITED",
                title="No page completed the automated WCAG audit",
                description="The accessibility scanner could not complete an axe-core audit on any crawled page.",
                severity=FindingSeverity.HIGH,
                selector_or_url=redact_url(target_url),
                suggested_fix="Check page reachability and whether the target's content security policy permits the audit runtime.",
            ))

        axe_score = sum(axe_scores) / len(axe_scores) if axe_scores else 0.0
        mobile_score = sum(mobile_scores) / len(mobile_scores) if mobile_scores else 0.0
        read_score = sum(read_scores) / len(read_scores) if read_scores else 0.0

        # Vision review remains on the start page; deterministic checks cover each crawled route.
        try:
            if page.url != target_url:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as error:
            accessibility_page_errors.append(f"{redact_url(target_url)}: {type(error).__name__}")
        vision_findings, vision_score = await UXVisionScanner.audit_visual_design(
            page, run_id=run_id, comparison_paths=comparison_paths
        )
        findings.extend(vision_findings)
        total_findings = len(findings)
        finding_severity_counts = {
            member.value: sum(finding.severity == member for finding in findings)
            for member in FindingSeverity
        }

        # Composite Pillar Score (Weighted: 50% WCAG, 20% Mobile, 15% Readability, 15% Vision UX)
        composite_score = (
            (axe_score * 0.50) +
            (mobile_score * 0.20) +
            (read_score * 0.15) +
            (vision_score * 0.15)
        )
        score = round(max(0.0, min(100.0, composite_score)), 1)
        duration = round(time.time() - start_time, 2)
        status = "PASSED" if score >= 80 else ("WARNING" if score >= 60 else "FAILED")
        crawl_limited = bool(crawl_result and (
            crawl_result.truncated_by_page_limit
            or crawl_result.truncated_by_timeout
            or crawl_result.truncated_by_link_limit
            or crawl_result.sitemap_truncated
            or crawl_result.skipped_depth_limit
            or crawl_result.skipped_unsafe
        ))
        crawl_pages_not_audited = max(0, len(crawl_result.pages) - pages_audited) if crawl_result else 0
        if not axe_scores:
            score = 0.0
            status = "FAILED"
        elif score < 60 or finding_severity_counts[FindingSeverity.CRITICAL.value]:
            status = "FAILED"
        elif (
            score < 80
            or finding_severity_counts[FindingSeverity.HIGH.value]
            or accessibility_page_errors
            or crawl_limited
            or crawl_pages_not_audited
        ):
            status = "WARNING"

        axe_metrics = {
            "violations_count": sum(item.get("violations_count", 0) for item in axe_metrics_by_page),
            "passes_count": sum(item.get("passes_count", 0) for item in axe_metrics_by_page),
            "axe_audited_pages": pages_audited,
        }

        return PillarScanResult(
            pillar=self.pillar_type,
            score=score,
            status=status,
            findings=findings[:1000],
            metrics={
                **axe_metrics,
                "mobile_responsive_score": mobile_score,
                "readability_score": read_score,
                "vision_ux_score": vision_score,
                "total_findings": total_findings,
                "finding_count": total_findings,
                "reported_findings": min(total_findings, 1000),
                "findings_truncated": total_findings > 1000,
                "finding_severity_counts": finding_severity_counts,
                "pages_attempted": len(audit_urls),
                "pages_audited": pages_audited,
                "crawl_pages_not_audited": crawl_pages_not_audited,
                "page_audit_errors": [redact_url(error) for error in accessibility_page_errors],
                "vision_review_scope": "starting page only",
                "score_unavailable": pages_audited == 0,
                "score_note": (
                    "No axe-core audit completed; 0 is not a measured zero-violation result."
                    if pages_audited == 0 else "Page scores average successful axe-core audits in this crawl."
                ),
                "crawl_coverage": crawl_result.report_summary() if crawl_result else {},
            },
            duration_sec=duration
        )
