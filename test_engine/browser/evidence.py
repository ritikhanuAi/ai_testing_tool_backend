import os
import re
import uuid
from pathlib import Path
from playwright.async_api import Page
from app.core.config import settings

class EvidenceCollector:
    """Captures and stores visual and telemetry evidence from browser sessions."""

    @staticmethod
    async def capture_screenshot(page: Page, run_id: str, case_id: str, full_page: bool = True) -> str:
        """Capture screenshot and save to storage/screenshots, returning relative path."""
        relative_path, _ = await EvidenceCollector.capture_screenshot_bytes(
            page, run_id, case_id, full_page=full_page
        )
        return relative_path

    @staticmethod
    async def capture_screenshot_bytes(
        page: Page,
        run_id: str,
        case_id: str,
        full_page: bool = True,
    ) -> tuple[str, bytes]:
        """Save a screenshot and return its storage-relative path and PNG bytes."""
        safe_run_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(run_id))[:100] or "run"
        safe_case_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(case_id))[:100] or "case"
        filename = f"{safe_run_id}_{safe_case_id}_{uuid.uuid4().hex[:8]}.png"
        screenshots_dir = os.path.join(settings.STORAGE_DIR, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)

        full_path = os.path.join(screenshots_dir, filename)
        image_bytes = await page.screenshot(path=full_path, full_page=full_page)

        # Return storage-relative path
        return os.path.join("screenshots", filename), image_bytes

    @staticmethod
    async def capture_download(download) -> tuple[str, int]:
        """Persist a completed browser download and reject empty artifacts."""
        original_name = Path(download.suggested_filename).name
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", original_name).strip("._")[:120] or "download.bin"
        filename = f"{uuid.uuid4().hex[:12]}_{safe_name}"
        downloads_dir = Path(settings.STORAGE_DIR) / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        full_path = downloads_dir / filename
        await download.save_as(str(full_path))
        size_bytes = full_path.stat().st_size
        if size_bytes <= 0:
            full_path.unlink(missing_ok=True)
            raise ValueError("The downloaded file was empty")
        return os.path.join("downloads", filename), size_bytes

    @staticmethod
    def read_screenshot(relative_path: str) -> bytes:
        """Read a previously captured screenshot without allowing paths outside storage."""
        root = Path(settings.STORAGE_DIR).resolve()
        candidate = (root / relative_path).resolve()
        if root not in candidate.parents:
            raise ValueError("Evidence path is outside the storage directory")
        return candidate.read_bytes()
