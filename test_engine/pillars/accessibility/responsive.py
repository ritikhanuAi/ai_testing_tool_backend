from playwright.async_api import Page
from typing import List, Tuple
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class ResponsiveViewportScanner:
    """Checks mobile layout behavior and flags unwanted horizontal scrolling."""

    @staticmethod
    async def check_mobile_overflow(page: Page) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []

        # Resize to mobile viewport
        await page.set_viewport_size({"width": 375, "height": 667})
        await page.wait_for_timeout(500)

        has_overflow = await page.evaluate("""
            () => document.documentElement.scrollWidth > document.documentElement.clientWidth
        """)

        if has_overflow:
            findings.append(PillarFinding(
                id="UX-MOBILE-HORIZONTAL-OVERFLOW",
                title="Unwanted horizontal scrolling on mobile viewport (375px)",
                description="Page content overflows the viewport width, forcing users to scroll horizontally.",
                severity=FindingSeverity.HIGH,
                suggested_fix="Ensure CSS max-width: 100% and overflow-x: hidden are applied to container elements."
            ))
            return findings, 75.0

        return findings, 100.0
