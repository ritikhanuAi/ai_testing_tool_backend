from playwright.async_api import Page
from typing import List, Tuple
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding
from ai_engine.prompts.ux_vision_evaluator import UXVisionEvaluator

class UXVisionScanner:
    """Dispatches Playwright viewport screenshots to STPI Qwen-27B Vision model for visual design review."""

    @staticmethod
    async def audit_visual_design(page: Page, run_id: str = "run") -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []

        try:
            # Capture full viewport screenshot as PNG bytes
            screenshot_bytes = await page.screenshot(type="png", full_page=False)

            # Pass to Vision Model
            issues = await UXVisionEvaluator.evaluate_screenshot(
                image_bytes=screenshot_bytes,
                viewport_label="Desktop Viewport"
            )

            deduction = 0.0
            for i, issue in enumerate(issues[:4]):
                sev_raw = issue.get("severity", "MEDIUM").upper()
                sev = getattr(FindingSeverity, sev_raw, FindingSeverity.MEDIUM)

                if sev == FindingSeverity.HIGH:
                    deduction += 10.0
                elif sev == FindingSeverity.MEDIUM:
                    deduction += 5.0
                else:
                    deduction += 2.0

                findings.append(PillarFinding(
                    id=f"UX-VISION-{i+1}",
                    title=f"Visual UX: {issue.get('title', 'Layout anomaly detected')}",
                    description=issue.get("description", "Visual layout imperfection identified by Vision AI."),
                    severity=sev,
                    suggested_fix=issue.get("suggested_fix", "Refactor CSS styling and element padding.")
                ))

            score = max(0.0, 100.0 - deduction)
            return findings, score

        except Exception:
            return findings, 100.0
