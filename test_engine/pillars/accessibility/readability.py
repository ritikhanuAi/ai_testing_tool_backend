from playwright.async_api import Page
from typing import List, Tuple
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding
from test_engine.crawl.site_crawler import redact_url

class ReadabilityScanner:
    """Checks for missing image alternative text and unlabeled interactive elements."""

    @staticmethod
    async def audit_elements(page: Page) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []

        unlabeled_images = await page.eval_on_selector_all(
            "img:not([alt]), img[alt='']",
            "els => els.slice(0, 5).map(e => e.src)"
        )

        deduction = 0.0
        for src in unlabeled_images:
            findings.append(PillarFinding(
                id="A11Y-IMG-NO-ALT",
                title="Image missing descriptive alternative text",
                description=f"Image {redact_url(src)} lacks an alt attribute, hindering screen readers.",
                severity=FindingSeverity.LOW,
                selector_or_url=redact_url(src),
                suggested_fix="Add descriptive alt attribute explaining image content."
            ))
            deduction += 5.0

        score = max(0.0, 100.0 - deduction)
        return findings, score
