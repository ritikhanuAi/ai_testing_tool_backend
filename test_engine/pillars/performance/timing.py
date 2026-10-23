from playwright.async_api import Page
from typing import Dict, Any, Tuple, List
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class NavigationTimingScanner:
    """Extracts W3C Navigation Timing metrics from browser."""

    @staticmethod
    async def extract_timing(page: Page) -> Tuple[Dict[str, float], List[PillarFinding]]:
        findings: List[PillarFinding] = []

        timing_data = await page.evaluate("""
            () => {
                const nav = performance.getEntriesByType('navigation')[0];
                if (!nav) return null;
                return {
                    dns_time: Math.round(nav.domainLookupEnd - nav.domainLookupStart),
                    tcp_time: Math.round(nav.connectEnd - nav.connectStart),
                    ttfb: Math.round(nav.responseStart - nav.requestStart),
                    download_time: Math.round(nav.responseEnd - nav.responseStart),
                    dom_interactive: Math.round(nav.domInteractive),
                    dom_content_loaded: Math.round(nav.domContentLoadedEventEnd),
                    load_time: Math.round(nav.loadEventEnd)
                };
            }
        """)

        metrics = timing_data or {}
        ttfb = metrics.get("ttfb", 0)
        load_time = metrics.get("load_time", 0)

        if ttfb > 1000:
            findings.append(PillarFinding(
                id="PERF-SLOW-TTFB",
                title=f"Slow Time-to-First-Byte (TTFB: {ttfb}ms)",
                description="Server took more than 1 second to begin responding.",
                severity=FindingSeverity.HIGH if ttfb > 2500 else FindingSeverity.MEDIUM,
                suggested_fix="Optimize database queries, enable backend caching, or utilize a CDN."
            ))

        if load_time > 4000:
            findings.append(PillarFinding(
                id="PERF-SLOW-LOAD",
                title=f"Slow full page load ({load_time}ms)",
                description="Full page load exceeded 4.0 seconds.",
                severity=FindingSeverity.MEDIUM,
                suggested_fix="Defer non-critical scripts and optimize images."
            ))

        return metrics, findings
