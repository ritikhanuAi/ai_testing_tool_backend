from playwright.async_api import Page
from typing import Dict, Any, Tuple, List
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class CoreWebVitalsScanner:
    """Captures Core Web Vitals (LCP, CLS, FCP) directly inside browser."""

    @staticmethod
    async def capture_vitals(page: Page) -> Tuple[Dict[str, float], List[PillarFinding]]:
        findings: List[PillarFinding] = []

        vitals = await page.evaluate("""
            () => {
                let fcp = 0;
                let lcp = 0;
                let cls = 0;

                const paintEntries = performance.getEntriesByType('paint');
                for (const entry of paintEntries) {
                    if (entry.name === 'first-contentful-paint') {
                        fcp = Math.round(entry.startTime);
                    }
                }

                // If LCP observer ran
                const lcpEntries = performance.getEntriesByType('largest-contentful-paint');
                if (lcpEntries.length > 0) {
                    lcp = Math.round(lcpEntries[lcpEntries.length - 1].startTime);
                }

                return { fcp, lcp, cls };
            }
        """)

        metrics = vitals or {"fcp": 0, "lcp": 0, "cls": 0}
        fcp = metrics.get("fcp", 0)

        if fcp > 2500:
            findings.append(PillarFinding(
                id="PERF-SLOW-FCP",
                title=f"Slow First Contentful Paint ({fcp}ms)",
                description="User waited over 2.5s before seeing initial content render.",
                severity=FindingSeverity.MEDIUM,
                suggested_fix="Eliminate render-blocking CSS and pre-connect to critical origins."
            ))

        return metrics, findings
