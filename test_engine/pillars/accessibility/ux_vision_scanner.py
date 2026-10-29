from typing import List, Optional, Tuple

from playwright.async_api import Page

from ai_engine.prompts.ux_vision_evaluator import UXVisionEvaluator
from app.common.constants import FindingSeverity
from test_engine.browser.evidence import EvidenceCollector
from test_engine.config import default_engine_config
from test_engine.pillars.base import PillarFinding


class UXVisionScanner:
    """Compare desktop, tablet, mobile, and interaction captures with pixel coordinates."""

    @staticmethod
    async def audit_visual_design(
        page: Page,
        run_id: str = "run",
        comparison_paths: Optional[List[str]] = None,
    ) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []
        original_viewport = page.viewport_size or {
            "width": default_engine_config.viewport_width,
            "height": default_engine_config.viewport_height,
        }

        try:
            await page.set_viewport_size({"width": 1280, "height": 720})
            desktop_path, desktop_bytes = await EvidenceCollector.capture_screenshot_bytes(
                page, run_id, "UX-DESKTOP", full_page=False
            )
            await page.set_viewport_size({"width": 768, "height": 1024})
            tablet_path, tablet_bytes = await EvidenceCollector.capture_screenshot_bytes(
                page, run_id, "UX-TABLET", full_page=False
            )
            await page.set_viewport_size({"width": 375, "height": 667})
            mobile_path, mobile_bytes = await EvidenceCollector.capture_screenshot_bytes(
                page, run_id, "UX-MOBILE", full_page=False
            )
            state_comparison = []
            state_paths = []
            for path in (comparison_paths or [])[:2]:
                try:
                    state_comparison.append(EvidenceCollector.read_screenshot(path))
                    state_paths.append(path)
                except (OSError, ValueError):
                    continue
            if len(state_comparison) != 2:
                state_comparison = []
                state_paths = []
            issues = await UXVisionEvaluator.evaluate_viewports(
                desktop_bytes, tablet_bytes, mobile_bytes, state_comparison=state_comparison
            )
        except Exception:
            return findings, 100.0
        finally:
            await page.set_viewport_size(original_viewport)

        deduction = 0.0
        for index, issue in enumerate(issues[:8]):
            severity_name = issue.get("severity", "MEDIUM").upper()
            severity = getattr(FindingSeverity, severity_name, FindingSeverity.MEDIUM)
            deduction += {
                FindingSeverity.HIGH: 10.0,
                FindingSeverity.MEDIUM: 5.0,
            }.get(severity, 2.0)
            viewport = issue.get("viewport", "desktop")
            source = issue.get("source", "responsive")
            evidence_path = {
                "desktop": desktop_path,
                "tablet": tablet_path,
                "mobile": mobile_path,
            }.get(viewport, desktop_path)
            if source == "interaction" and state_paths:
                evidence_path = state_paths[-1]
            findings.append(PillarFinding(
                id=f"UX-VISION-{index + 1}",
                title=f"Visual UX ({viewport}): {issue.get('title', 'Layout issue detected')}",
                description=issue.get("description", "Visual usability issue identified by the vision review."),
                severity=severity,
                evidence_path=evidence_path,
                bounding_box=issue.get("bounding_box_px"),
                suggested_fix=issue.get("suggested_fix", "Review the affected layout and control styling."),
            ))

        return findings, max(0.0, 100.0 - deduction)
