import os
import uuid
from playwright.async_api import Page
from app.core.config import settings

class EvidenceCollector:
    """Captures and stores visual and telemetry evidence from browser sessions."""

    @staticmethod
    async def capture_screenshot(page: Page, run_id: str, case_id: str, full_page: bool = True) -> str:
        """Capture screenshot and save to storage/screenshots, returning relative path."""
        filename = f"{run_id}_{case_id}_{uuid.uuid4().hex[:8]}.png"
        screenshots_dir = os.path.join(settings.STORAGE_DIR, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)

        full_path = os.path.join(screenshots_dir, filename)
        await page.screenshot(path=full_path, full_page=full_page)

        # Return storage-relative path
        return os.path.join("screenshots", filename)
