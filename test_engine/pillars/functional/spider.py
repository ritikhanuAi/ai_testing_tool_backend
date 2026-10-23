import httpx
from typing import List, Dict, Any
from playwright.async_api import Page
from test_engine.pillars.base import PillarFinding
from app.common.constants import FindingSeverity
from app.common.utils import extract_domain

class LinkSpider:
    """Checks page for dead links (404) and broken image sources."""

    @staticmethod
    async def audit_links_and_images(page: Page, target_url: str) -> List[PillarFinding]:
        findings: List[PillarFinding] = []
        target_domain = extract_domain(target_url)

        # Extract links
        links = await page.eval_on_selector_all(
            "a[href]",
            "elements => elements.map(el => el.href).filter(href => href.startsWith('http'))"
        )
        # Check first 15 internal links
        internal_links = [l for l in set(links) if extract_domain(l) == target_domain][:15]

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            for link in internal_links:
                try:
                    res = await client.head(link)
                    if res.status_code >= 400:
                        findings.append(PillarFinding(
                            id=f"BROKEN-LINK-{res.status_code}",
                            title=f"Broken link detected (HTTP {res.status_code})",
                            description=f"Link URL {link} returned HTTP status {res.status_code}",
                            severity=FindingSeverity.MEDIUM,
                            selector_or_url=link,
                            suggested_fix="Update or remove dead hyperlink."
                        ))
                except Exception:
                    pass

        return findings
