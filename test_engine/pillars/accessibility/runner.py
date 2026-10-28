import time
from typing import List, Dict, Any, Optional
from playwright.async_api import Page
from app.common.constants import PillarType
from test_engine.pillars.base import BasePillarScanner, PillarScanResult, PillarFinding
from test_engine.pillars.accessibility.axe_runner import AxeCoreScanner
from test_engine.pillars.accessibility.responsive import ResponsiveViewportScanner
from test_engine.pillars.accessibility.readability import ReadabilityScanner
from test_engine.pillars.accessibility.ux_vision_scanner import UXVisionScanner

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
        **kwargs
    ) -> PillarScanResult:
        start_time = time.time()
        findings: List[PillarFinding] = []

        # 1. Deterministic WCAG 2.1 AA Audit + AI Code Fix Generation
        axe_findings, axe_score, axe_metrics = await AxeCoreScanner.run_axe(page)
        findings.extend(axe_findings)

        # 2. Responsive Mobile Layout Check (375px)
        mobile_findings, mobile_score = await ResponsiveViewportScanner.check_mobile_overflow(page)
        findings.extend(mobile_findings)

        # Reset viewport back to desktop for visual review
        await page.set_viewport_size({"width": 1280, "height": 720})

        # 3. Readability & Alt Text Check
        read_findings, read_score = await ReadabilityScanner.audit_elements(page)
        findings.extend(read_findings)

        # 4. Multimodal Vision AI Review (Screenshots reviewed by Qwen-27B on STPI)
        vision_findings, vision_score = await UXVisionScanner.audit_visual_design(page, run_id=run_id)
        findings.extend(vision_findings)

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

        return PillarScanResult(
            pillar=self.pillar_type,
            score=score,
            status=status,
            findings=findings,
            metrics={
                **axe_metrics,
                "mobile_responsive_score": mobile_score,
                "readability_score": read_score,
                "vision_ux_score": vision_score,
                "total_findings": len(findings)
            },
            duration_sec=duration
        )
