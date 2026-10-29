import math
from typing import Any, Dict, List, Optional

from app.common.constants import FindingSeverity, PillarType
from test_engine.crawl.site_crawler import CrawlResult, CrawledPage, redact_url
from test_engine.pillars.base import BasePillarScanner, PillarFinding, PillarScanResult
from test_engine.scoring.rubrics import calculate_pillar_deductions


class PerformanceScanner(BasePillarScanner):
    """Grade page-load lab metrics and resource costs across the discovered site."""

    MAX_REPORTED_FINDINGS = 200

    @property
    def pillar_type(self) -> PillarType:
        return PillarType.PERFORMANCE

    @staticmethod
    def _percentile_75(values: List[float]) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, math.ceil(len(ordered) * 0.75) - 1)
        return round(ordered[index], 2)

    @classmethod
    def _page_findings(cls, page: CrawledPage, metrics: Dict[str, Any]) -> List[PillarFinding]:
        findings: List[PillarFinding] = []
        url = page.final_url or page.url

        def add(metric: str, value: Any, threshold: Any, severity: FindingSeverity, description: str, fix: str) -> None:
            findings.append(PillarFinding(
                id=f"PERF-{metric.upper().replace('_', '-')}",
                title=f"{metric.upper().replace('_', ' ')} is above the platform threshold",
                description=f"{description} Measured {value}; threshold is {threshold}.",
                severity=severity,
                selector_or_url=redact_url(url),
                suggested_fix=fix,
            ))

        lcp = metrics.get("lcp_ms")
        if lcp is not None and lcp > 2500:
            add("lcp", f"{lcp} ms", "2,500 ms good / 4,000 ms poor", FindingSeverity.HIGH if lcp > 4000 else FindingSeverity.MEDIUM,
                "Largest Contentful Paint exceeded the good Core Web Vitals threshold.",
                "Prioritize the largest visible image or text block, reduce server response time, and remove render-blocking work.")

        cls_value = metrics.get("cls")
        if cls_value is not None and cls_value > 0.1:
            add("cls", cls_value, "0.1 good / 0.25 poor", FindingSeverity.HIGH if cls_value > 0.25 else FindingSeverity.MEDIUM,
                "Cumulative Layout Shift exceeded the good Core Web Vitals threshold.",
                "Reserve layout space for images, embeds, ads, and content inserted after initial render.")

        inp = metrics.get("inp_ms")
        if inp is not None and inp > 200:
            add("inp", f"{inp} ms", "200 ms good / 500 ms poor", FindingSeverity.HIGH if inp > 500 else FindingSeverity.MEDIUM,
                "Measured interaction latency exceeded the good Core Web Vitals threshold.",
                "Reduce long main-thread tasks and keep event handlers small.")

        ttfb = metrics.get("ttfb_ms")
        if ttfb is not None and ttfb > 800:
            add("ttfb", f"{ttfb} ms", "800 ms", FindingSeverity.HIGH if ttfb > 1800 else FindingSeverity.MEDIUM,
                "Time to First Byte is high for this lab navigation.",
                "Inspect backend processing, caching, DNS, connection setup, and geographic network latency.")

        fcp = metrics.get("fcp_ms")
        if fcp is not None and fcp > 1800:
            add("fcp", f"{fcp} ms", "1,800 ms", FindingSeverity.HIGH if fcp > 3000 else FindingSeverity.MEDIUM,
                "First Contentful Paint is slow for this lab navigation.",
                "Reduce render-blocking CSS and JavaScript and prioritize above-the-fold content.")

        tbt = metrics.get("tbt_ms")
        if tbt is not None and tbt > 200:
            add("total_blocking_time", f"{tbt} ms", "200 ms", FindingSeverity.HIGH if tbt > 600 else FindingSeverity.MEDIUM,
                "Long tasks blocked the main thread during page loading.",
                "Split large JavaScript tasks and defer non-critical work.")

        load = metrics.get("load_ms")
        if load is not None and load > 4000:
            add("load", f"{load} ms", "4,000 ms", FindingSeverity.MEDIUM,
                "The page load event completed slowly in this lab navigation.",
                "Reduce critical resource count and defer non-essential resources.")

        transfer = metrics.get("transfer_size_bytes")
        if transfer is not None and transfer > 3 * 1024 * 1024:
            mb = round(transfer / (1024 * 1024), 2)
            add("transfer_size", f"{mb} MB", "3 MB", FindingSeverity.MEDIUM,
                "Measured page resource transfer exceeded the platform budget.",
                "Compress images, ship less JavaScript, and enable efficient text and asset compression.")

        request_count = metrics.get("resource_count")
        if request_count is not None and request_count > 100:
            add("request_count", request_count, 100, FindingSeverity.LOW,
                "The page loaded a high number of browser resources.",
                "Combine or remove unnecessary requests and lazy-load below-the-fold resources.")

        for asset in metrics.get("oversized_assets", [])[:20]:
            size = asset.get("size_bytes", 0)
            findings.append(PillarFinding(
                id="PERF-OVERSIZED-ASSET",
                title=f"Large resource ({round(size / 1024)} KB)",
                description=f"The browser reports a resource larger than 1 MB: {redact_url(asset.get('url', ''))}",
                severity=FindingSeverity.LOW if size < 5 * 1024 * 1024 else FindingSeverity.MEDIUM,
                selector_or_url=redact_url(asset.get("url") or url),
                suggested_fix="Resize or compress this asset and serve a responsive variant where appropriate.",
            ))
        return findings

    async def scan(
        self,
        page,
        target_url: str,
        crawl_result: Optional[CrawlResult] = None,
        **kwargs: Any,
    ) -> PillarScanResult:
        pages = crawl_result.pages if crawl_result else []
        page_metrics: List[Dict[str, Any]] = []
        findings: List[PillarFinding] = []
        for crawled_page in pages:
            metrics = dict(crawled_page.page_observations.get("performance", {}))
            metrics["oversized_assets"] = [
                {**asset, "url": redact_url(asset.get("url", ""))}
                for asset in metrics.get("oversized_assets", [])
            ]
            page_metrics.append({"url": redact_url(crawled_page.final_url), **metrics})
            findings.extend(self._page_findings(crawled_page, metrics))

        successful_pages = sum(page.navigation_error is None for page in pages)
        metric_keys = ("fcp_ms", "lcp_ms", "cls", "inp_ms", "ttfb_ms", "load_ms", "tbt_ms", "resource_count")
        measured_pages = sum(any(metrics.get(key) is not None for key in metric_keys) for metrics in (
            item.page_observations.get("performance", {}) for item in pages
        ))
        if not pages or not measured_pages:
            findings.append(PillarFinding(
                id="PERF-CRAWL-NO-USABLE-PAGES",
                title="Performance pillar could not measure a page",
                description="No crawled page produced usable browser observations, so a performance grade cannot be trusted.",
                severity=FindingSeverity.HIGH,
                selector_or_url=redact_url(target_url),
                suggested_fix="Check target reachability, authorization, and crawl coverage before relying on a performance score.",
            ))

        score, deductions = calculate_pillar_deductions(findings)
        reported_findings = findings[:self.MAX_REPORTED_FINDINGS]
        metric_names = ("lcp_ms", "cls", "inp_ms", "fcp_ms", "ttfb_ms", "load_ms", "tbt_ms")
        p75 = {
            name: self._percentile_75([
                float(item[name]) for item in page_metrics
                if item.get(name) is not None
            ])
            for name in metric_names
        }
        inp_coverage = sum(item.get("inp_ms") is not None for item in page_metrics)
        severity = {
            member.value: sum(finding.severity == member for finding in findings)
            for member in FindingSeverity
        }
        crawl_limited = bool(crawl_result and (
            crawl_result.truncated_by_page_limit
            or crawl_result.truncated_by_timeout
            or crawl_result.truncated_by_link_limit
            or crawl_result.sitemap_truncated
            or crawl_result.skipped_depth_limit
            or crawl_result.skipped_unsafe
        ))
        if not measured_pages or score < 60:
            status = "FAILED"
        elif score < 85 or severity[FindingSeverity.HIGH.value] or crawl_limited or successful_pages < len(pages):
            status = "WARNING"
        else:
            status = "PASSED"
        return PillarScanResult(
            pillar=self.pillar_type,
            score=score,
            status=status,
            findings=reported_findings,
            metrics={
                "measurement_type": "browser_lab",
                "crawl_pages_scanned": len(pages),
                "crawl_pages_attempted": len(pages),
                "crawl_pages_with_observations": successful_pages,
                "crawl_pages_with_performance_metrics": measured_pages,
                "crawl_pages_with_navigation_errors": len(pages) - successful_pages,
                "site_page_p75": p75,
                "site_page_p75_sample_counts": {
                    name: sum(item.get(name) is not None for item in page_metrics)
                    for name in metric_names
                },
                "inp_pages_measured": inp_coverage,
                "inp_pages_unmeasured": max(0, len(page_metrics) - inp_coverage),
                "page_metrics": page_metrics,
                "finding_count": len(findings),
                "finding_severity_counts": severity,
                "reported_finding_count": len(reported_findings),
                "findings_truncated": len(findings) > len(reported_findings),
                "findings_by_severity": {
                    member.name.lower(): sum(finding.severity == member for finding in reported_findings)
                    for member in FindingSeverity
                },
                "score_deductions": deductions,
                "crawl_coverage": crawl_result.report_summary() if crawl_result else {},
                "grade_scope": "discovered pages measured in this browser lab run; not real-user field data",
                "score_unavailable": measured_pages == 0,
                "score_note": (
                    "No usable performance measurements were collected."
                    if measured_pages == 0 else "Score deductions are based on measured pages in this browser lab run."
                ),
            },
        )
