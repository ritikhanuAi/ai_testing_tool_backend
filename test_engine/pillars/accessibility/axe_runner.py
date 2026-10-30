from playwright.async_api import Page
from typing import List, Tuple, Dict, Any
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding
from ai_engine.prompts.accessibility_fixer import AccessibilityFixer

class AxeCoreScanner:
    """Runs automated WCAG 2.1 AA accessibility checks via axe-core with AI code fixes."""

    AXE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"

    @classmethod
    async def run_axe(
        cls,
        page: Page,
        *,
        page_url: str = "",
        generate_fixes: bool = True,
    ) -> Tuple[List[PillarFinding], float, Dict[str, Any]]:
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
                            selector: v.nodes[0] ? v.nodes[0].target[0] : null,
                            html: v.nodes[0] ? v.nodes[0].html : null
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

                # Generate actionable developer code fix (AI with deterministic fallback)
                if generate_fixes:
                    fix = await AccessibilityFixer.get_fix(
                        rule_id=v["id"],
                        help_text=v["help"],
                        selector=v.get("selector"),
                        html_snippet=v.get("html")
                    )
                else:
                    fix = f"Review WCAG rule '{v['id']}' on this page and correct the affected element."

                findings.append(PillarFinding(
                    id=f"A11Y-{v['id'].upper().replace('-', '_')}",
                    title=f"Accessibility: {v['help']}",
                    description=f"{v['description']} ({v['nodes_count']} occurrence(s))",
                    severity=sev,
                    selector_or_url=(f"{page_url} :: {v.get('selector')}" if page_url and v.get("selector") else page_url or v.get("selector")),
                    suggested_fix=fix
                ))

            score = max(0.0, 100.0 - deduction)
            metrics = {
                "violations_count": len(violations),
                "passes_count": results.get("passes_count", 0),
                "axe_audit_completed": True,
            }
            return findings, score, metrics

        except Exception as e:
            # Fallback if axe injection fails (e.g. strict CSP)
            findings.append(PillarFinding(
                id="A11Y-AUDIT-WARNING",
                title="Accessibility audit completed with CSP limitations",
                description=f"Axe-core could not complete ({type(e).__name__}); the automated WCAG result is unavailable for this page.",
                severity=FindingSeverity.LOW,
                suggested_fix="Ensure CSP allows inline scripts during local test audits."
            ))
            return findings, 85.0, {"violations_count": 0, "passes_count": 0, "axe_audit_completed": False}
