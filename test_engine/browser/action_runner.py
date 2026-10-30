import time
from playwright.async_api import Page
from test_engine.schema.actions import ActionStep, ActionType
from test_engine.browser.locators import resolve_locator
from test_engine.browser.evidence import EvidenceCollector
from typing import Dict, Any
import logging

logger = logging.getLogger("test_engine.browser.action_runner")

class ActionRunner:
    """Executes discrete browser actions via Playwright and tracks timing and outcomes."""

    @staticmethod
    async def execute_step(page: Page, step: ActionStep) -> Dict[str, Any]:
        """Execute a single browser action step deterministically."""
        start_time = time.time()
        timeout = step.timeout_ms or 10000

        async def perform(locator=None) -> Dict[str, Any]:
            if step.action == ActionType.OPEN_PAGE:
                response = await page.goto(step.value, timeout=timeout, wait_until="domcontentloaded")
                status_code = response.status if response else 200
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Page navigated successfully (HTTP {status_code})"
                }

            elif step.action == ActionType.CLICK:
                await locator.click(timeout=timeout)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Clicked target '{step.target}'"
                }

            elif step.action == ActionType.TYPE_TEXT:
                await locator.fill(str(step.value or ""), timeout=timeout)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Typed text into '{step.target}'"
                }

            elif step.action == ActionType.SELECT_OPTION:
                try:
                    await locator.select_option(label=str(step.value), timeout=timeout)
                except Exception:
                    await locator.select_option(value=str(step.value), timeout=timeout)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Selected option '{step.value}'"
                }

            elif step.action == ActionType.DOWNLOAD:
                async with page.expect_download(timeout=timeout) as download_info:
                    await locator.click(timeout=timeout)
                download = await download_info.value
                download_path, size_bytes = await EvidenceCollector.capture_download(download)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Downloaded {download.suggested_filename} ({size_bytes} bytes)",
                    "download_path": download_path,
                    "size_bytes": size_bytes,
                }

            elif step.action == ActionType.WAIT:
                wait_ms = int(step.value) if step.value and str(step.value).isdigit() else 1000
                await page.wait_for_timeout(wait_ms)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Waited {wait_ms}ms"
                }

            elif step.action == ActionType.HOVER:
                await locator.hover(timeout=timeout)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Hovered over '{step.target}'"
                }

            elif step.action == ActionType.SCROLL:
                await page.evaluate("window.scrollBy(0, 500)")
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": "Scrolled down 500px"
                }

            else:
                return {
                    "action": step.action.value,
                    "status": "FAILED",
                    "details": f"Action {step.action.value} is not implemented",
                    "error": f"Action {step.action.value} is not implemented",
                }

        locator_missing = bool(step.target)
        try:
            primary = resolve_locator(page, step.target) if step.target else None
            if primary is not None:
                primary_count = await primary.count()
                locator_missing = primary_count == 0 or (primary_count == 1 and not await primary.is_visible())
            result = await perform(primary)
        except Exception as initial_error:
            result = None
            if locator_missing and step.fallback_role and step.fallback_name:
                fallback = page.get_by_role(step.fallback_role, name=step.fallback_name, exact=True)
                try:
                    if await fallback.count() == 1 and await fallback.is_visible():
                        result = await perform(fallback)
                        result["details"] += " (recovered using the accessible role and name)"
                        result["healed"] = True
                        logger.warning(
                            "Recovered failed selector with role=%s name=%r",
                            step.fallback_role,
                            step.fallback_name,
                        )
                except Exception:
                    result = None
            if result is None:
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "FAILED",
                    "duration_sec": round(duration, 3),
                    "error": str(initial_error),
                }

        result["duration_sec"] = round(time.time() - start_time, 3)
        return result
