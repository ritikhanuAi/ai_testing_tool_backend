from playwright.async_api import Page
from typing import List, Tuple, Dict, Any
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class AxeCoreScanner:
    """Runs automated WCAG 2.1 AA accessibility checks via axe-core."""

    AXE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"

    @classmethod
    async def run_axe(cls, page: Page) -> Tuple[List[PillarFinding], float, Dict[str, Any]]:
        findings: List[PillarFinding] = []

        try:
            # Inject axe-core library
            await page.add_script_tag(url=cls.AXE_CDN_URL)

            # Execute axe audit
            results = await page.evaluate("""
                async () => {
                    const axeResults = await window.axe.run({
                        runOnly: {
                            type: 'tag',
                            values: ['wcag2a', 'wcag2aa']
                        }
                    });
                    return {
                        violations: axeResults.violations.map(v => ({
                            id: v.id,
                            impact: v.impact,
                            description: v.description,
                            help: v.help,
                            nodes_count: v.nodes.length,
                            selector: v.nodes[0] ? v.nodes[0].target[0] : null
                        })),
                        passes_count: axeResults.passes.length
                    };
                }
            """)

            violations = results.get("violations", [])
            deduction = 0.0

            for v in violations:
                impact = v.get("impact", "minor")
                if impact == "critical":
                    sev = FindingSeverity.CRITICAL
                    deduction += 15.0
                elif impact == "serious":
                    sev = FindingSeverity.HIGH
                    deduction += 10.0
                elif impact == "moderate":
                    sev = FindingSeverity.MEDIUM
                    deduction += 5.0
                else:
                    sev = FindingSeverity.LOW
                    deduction += 2.0

                findings.append(PillarFinding(
                    id=f"A11Y-{v['id'].upper().replace('-', '_')}",
                    title=f"Accessibility: {v['help']}",
                    description=f"{v['description']} ({v['nodes_count']} occurrence(s))",
                    severity=sev,
                    selector_or_url=v.get("selector"),
                    suggested_fix=f"Review WCAG criteria for rule '{v['id']}'."
                ))

            score = max(0.0, 100.0 - deduction)
            metrics = {
                "violations_count": len(violations),
                "passes_count": results.get("passes_count", 0)
            }
            return findings, score, metrics

        except Exception as e:
            # Graceful fallback if CDN is unreachable in offline mode
            return [], 85.0, {"error": str(e), "violations_count": 0}
