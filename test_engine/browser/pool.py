from playwright.async_api import async_playwright, Playwright, Browser
from typing import Optional, Dict
from test_engine.config import default_engine_config

class BrowserPool:
    """Manages Playwright lifecycle and headless browser instances."""

    _playwright: Optional[Playwright] = None
    _browsers: Dict[str, Browser] = {}

    @classmethod
    async def get_browser(cls, browser_type: Optional[str] = None) -> Browser:
        """Get or initialize singleton Playwright browser instance for requested engine."""
        if cls._playwright is None:
            cls._playwright = await async_playwright().start()

        b_type = (browser_type or default_engine_config.browser_type or "chromium").lower()
        if b_type not in ["chromium", "firefox", "webkit"]:
            b_type = "chromium"

        current_browser = cls._browsers.get(b_type)
        if current_browser is None or not current_browser.is_connected():
            if b_type == "firefox":
                cls._browsers[b_type] = await cls._playwright.firefox.launch(headless=default_engine_config.headless)
            elif b_type == "webkit":
                cls._browsers[b_type] = await cls._playwright.webkit.launch(headless=default_engine_config.headless)
            else:
                cls._browsers[b_type] = await cls._playwright.chromium.launch(
                    headless=default_engine_config.headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
        return cls._browsers[b_type]

    @classmethod
    async def close(cls):
        """Clean shutdown of browser and playwright engine."""
        for b in cls._browsers.values():
            if b and b.is_connected():
                await b.close()
        cls._browsers.clear()
        if cls._playwright:
            await cls._playwright.stop()
            cls._playwright = None
