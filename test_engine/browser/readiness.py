from playwright.async_api import Page


async def wait_for_page_ready(page: Page, timeout_ms: int = 12000) -> bool:
    """Wait for common app loading states to clear after the document is committed.

    A DOMContentLoaded event is not enough for client-rendered pages: their controls
    can still be covered by a full-page spinner while API requests are in flight.
    The wait is bounded so a broken app cannot hold a scan indefinitely.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 5000))
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
    except Exception:
        pass

    try:
        await page.wait_for_function(
            r"""() => {
                const visible = element => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                        Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
                };
                const loadingSelectors = [
                    '[aria-busy="true"]', '[data-loading="true"]',
                    '[role="progressbar"]', '[class*="spinner" i]',
                    '[class*="loading-overlay" i]', '[class*="loading-screen" i]'
                ];
                if (loadingSelectors.some(selector =>
                    Array.from(document.querySelectorAll(selector)).some(visible))) return false;

                const visibleText = Array.from(document.querySelectorAll('body *'))
                    .filter(element => visible(element) && element.children.length === 0)
                    .map(element => (element.textContent || '').trim().replace(/\s+/g, ' ').toLowerCase());
                return !visibleText.some(text => /^(please wait|loading(?:\.{0,3})?)$/.test(text));
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False
