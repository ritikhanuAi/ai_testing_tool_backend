from playwright.async_api import async_playwright, Playwright, Browser
from typing import Optional, Dict, Any
from test_engine.config import default_engine_config

class BrowserPool:
    """Manages Playwright lifecycle and headless browser instances."""

    _playwright: Optional[Playwright] = None
    _browsers: Dict[str, Browser] = {}

    @classmethod
    async def get_browser(
        cls,
        browser_type: Optional[str] = None,
        headless: Optional[bool] = None,
        slow_mo: Optional[int] = None,
    ) -> Browser:
        """Get or initialize singleton Playwright browser instance for requested engine and display mode."""
        if cls._playwright is None:
            cls._playwright = await async_playwright().start()

        b_type = (browser_type or default_engine_config.browser_type or "chromium").lower()
        if b_type not in ["chromium", "firefox", "webkit"]:
            b_type = "chromium"

        is_headless = default_engine_config.headless if headless is None else bool(headless)
        effective_slow_mo = slow_mo if slow_mo is not None else (0 if is_headless else 300)

        cache_key = f"{b_type}_h{is_headless}_s{effective_slow_mo}"
        current_browser = cls._browsers.get(cache_key)
        if current_browser is None or not current_browser.is_connected():
            launch_kwargs: Dict[str, Any] = {
                "headless": is_headless,
                "slow_mo": effective_slow_mo,
            }
            if b_type == "firefox":
                cls._browsers[cache_key] = await cls._playwright.firefox.launch(**launch_kwargs)
            elif b_type == "webkit":
                cls._browsers[cache_key] = await cls._playwright.webkit.launch(**launch_kwargs)
            else:
                args = ["--no-sandbox", "--disable-dev-shm-usage"]
                if is_headless:
                    args.append("--disable-gpu")
                cls._browsers[cache_key] = await cls._playwright.chromium.launch(
                    **launch_kwargs,
                    args=args
                )
        return cls._browsers[cache_key]

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
