from typing import Any, Dict

from playwright.async_api import Page

from test_engine.pillars.functional.spider import FunctionalSpider


class PageSummaryExtractor:
    """Extract concise page text plus the shared interactive element catalog."""

    @staticmethod
    async def extract_summary(page: Page) -> Dict[str, Any]:
        summary = await page.evaluate(r"""() => ({
            title: document.title || '',
            headings: Array.from(document.querySelectorAll('h1, h2'))
                .map(item => item.innerText.trim()).filter(Boolean).slice(0, 8),
            visible_feedback: Array.from(document.querySelectorAll(
                '[role="alert"], [role="status"], [aria-live="polite"], [aria-live="assertive"], [role="dialog"], [aria-modal="true"]'
            )).filter(item => {
                const style = getComputedStyle(item);
                return style.display !== 'none' && style.visibility !== 'hidden' && item.getClientRects().length > 0;
            }).map(item => item.innerText.replace(/\s+/g, ' ').trim().slice(0, 400)).filter(Boolean).slice(0, 6)
        })""")
        inventory = await FunctionalSpider.extract_interactive_inventory(page)
        return {
            **(summary or {}),
            "forms": inventory.get("forms", []),
            "interactive_elements": inventory.get("elements", []),
        }
