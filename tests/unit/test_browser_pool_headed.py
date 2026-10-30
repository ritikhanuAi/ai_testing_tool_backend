import pytest
from unittest.mock import AsyncMock, MagicMock
from test_engine.browser.pool import BrowserPool

@pytest.mark.asyncio
async def test_browser_pool_headed_launch_parameters():
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.stop = AsyncMock()

    # Reset any existing pool state
    await BrowserPool.close()
    BrowserPool._playwright = mock_playwright

    try:
        # Request headed browser with slow_mo=300
        browser = await BrowserPool.get_browser(browser_type="chromium", headless=False, slow_mo=300)
        assert browser == mock_browser

        mock_chromium.launch.assert_called_once()
        _, kwargs = mock_chromium.launch.call_args
        assert kwargs["headless"] is False
        assert kwargs["slow_mo"] == 300
        # In headed mode, --disable-gpu is omitted
        assert "--disable-gpu" not in kwargs.get("args", [])
        assert "--no-sandbox" in kwargs.get("args", [])
    finally:
        await BrowserPool.close()


@pytest.mark.asyncio
async def test_browser_pool_headless_default():
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    mock_browser.close = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.stop = AsyncMock()

    await BrowserPool.close()
    BrowserPool._playwright = mock_playwright

    try:
        browser = await BrowserPool.get_browser(browser_type="chromium", headless=True)
        assert browser == mock_browser

        mock_chromium.launch.assert_called_once()
        _, kwargs = mock_chromium.launch.call_args
        assert kwargs["headless"] is True
        assert kwargs["slow_mo"] == 0
        assert "--disable-gpu" in kwargs.get("args", [])
    finally:
        await BrowserPool.close()
