from playwright.async_api import async_playwright, Playwright, Browser
from typing import Optional
from test_engine.config import default_engine_config

class BrowserPool:
    """Manages Playwright lifecycle and headless browser instances."""

    _playwright: Optional[Playwright] = None
    _browser: Optional[Browser] = None

    @classmethod
    async def get_browser(cls) -> Browser:
        """Get or initialize singleton Playwright browser instance."""
        if cls._playwright is None:
            cls._playwright = await async_playwright().start()

        if cls._browser is None or not cls._browser.is_connected():
            b_type = default_engine_config.browser_type.lower()
            if b_type == "firefox":
                cls._browser = await cls._playwright.firefox.launch(headless=default_engine_config.headless)
            elif b_type == "webkit":
                cls._browser = await cls._playwright.webkit.launch(headless=default_engine_config.headless)
            else:
                cls._browser = await cls._playwright.chromium.launch(
                    headless=default_engine_config.headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
        return cls._browser

    @classmethod
    async def close(cls):
        """Clean shutdown of browser and playwright engine."""
        if cls._browser and cls._browser.is_connected():
            await cls._browser.close()
            cls._browser = None
        if cls._playwright:
            await cls._playwright.stop()
            cls._playwright = None
