import json
import time
from playwright.async_api import Page
from test_engine.schema.actions import ActionStep, ActionType
from test_engine.browser.locators import resolve_locator
from test_engine.browser.evidence import EvidenceCollector
from typing import Dict, Any
import logging
import mimetypes

logger = logging.getLogger("test_engine.browser.action_runner")


async def _find_custom_option(page: Page, trigger, option_name: str):
    """Find a visible custom-menu item, preferring the popup owned by its trigger."""
    scopes = []
    controls = (await trigger.get_attribute("aria-controls") or "").split()
    for popup_id in controls:
        scopes.append(page.locator(f"[id={json.dumps(popup_id)}]"))

    visible_popups = page.locator(
        '[role="listbox"]:visible, [role="menu"]:visible, [role="option"]:visible, '
        '[role="menuitem"]:visible, [data-radix-popper-content-wrapper]:visible, [data-state="open"]:visible'
    )
    for index in range(await visible_popups.count()):
        scopes.append(visible_popups.nth(index))

    expanded = (await trigger.get_attribute("aria-expanded") or "").lower() == "true"
    popup_open = expanded or await visible_popups.count() > 0
    for scope in scopes:
        try:
            popup_open = popup_open or await scope.is_visible()
        except Exception:
            continue
    # Some custom widgets render an open portal without ARIA roles. Use a page-wide
    # text fallback only after verifying that the trigger actually exposed a popup.
    if popup_open:
        scopes.append(page.locator("body"))
    for scope in scopes:
        for role in ("option", "menuitem", "button"):
            candidate = scope.get_by_role(role, name=option_name, exact=True)
            for index in range(await candidate.count()):
                item = candidate.nth(index)
                if await item.is_visible():
                    return item
        text_matches = scope.get_by_text(option_name, exact=True)
        for index in range(await text_matches.count()):
            item = text_matches.nth(index)
            if await item.is_visible():
                return item
    return None

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

            elif step.action == ActionType.UPLOAD_FILE:
                raw = str(step.value or "test_upload.txt")
                try:
                    payload = json.loads(raw)
                    filename = payload.get("name", "test_upload.txt")
                    mime_type = payload.get("mimeType") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    content = payload.get("content", f"Test upload: {filename}")
                except (json.JSONDecodeError, ValueError):
                    filename = raw
                    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    content = f"Test upload: {filename}"
                await locator.set_input_files(
                    {"name": filename, "mimeType": mime_type, "buffer": content.encode()},
                    timeout=timeout,
                )
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Uploaded file '{filename}' ({mime_type})",
                }

            elif step.action == ActionType.SELECT_OPTION:
                tag_name = await locator.evaluate("element => element.tagName.toLowerCase()")
                if tag_name == "select":
                    if step.select_by_value:
                        await locator.select_option(value=str(step.value), timeout=timeout)
                    else:
                        try:
                            await locator.select_option(label=str(step.value), timeout=timeout)
                        except Exception:
                            await locator.select_option(value=str(step.value), timeout=timeout)
                else:
                    await locator.click(timeout=timeout)
                    option_name = str(step.value)
                    option = await _find_custom_option(page, locator, option_name)
                    if option is None:
                        # Allow the UI a short render window for portals that mount
                        # their menu asynchronously after the trigger click.
                        try:
                            await page.get_by_text(option_name, exact=True).first.wait_for(
                                state="visible", timeout=min(timeout, 2000)
                            )
                        except Exception:
                            pass
                        option = await _find_custom_option(page, locator, option_name)
                    if option is None:
                        raise TimeoutError(
                            f"Could not select {option_name!r}: no visible matching option appeared after clicking the dropdown. "
                            "The control may not have opened, may use unsupported markup, or its options may have changed."
                        )
                    await option.click(timeout=timeout)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Selected option '{step.value}'"
                }

            elif step.action == ActionType.SET_CHECKED:
                checked = str(step.value).strip().lower() in {"true", "1", "yes", "checked"}
                tag_name = await locator.evaluate("element => element.tagName.toLowerCase()")
                role = await locator.get_attribute("role")
                if tag_name == "input" and await locator.get_attribute("type") in {"checkbox", "radio"}:
                    await locator.set_checked(checked, timeout=timeout)
                    actual = await locator.is_checked(timeout=timeout)
                elif role in {"checkbox", "switch", "radio"}:
                    before = (await locator.get_attribute("aria-checked")) == "true"
                    if before != checked:
                        await locator.click(timeout=timeout)
                    actual = (await locator.get_attribute("aria-checked")) == "true"
                else:
                    raise ValueError("set_checked requires a checkbox, switch, or radio control")
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED" if actual == checked else "FAILED",
                    "details": f"Set checked state to {checked}; observed {actual}",
                    "expected": checked,
                    "actual": actual,
                }

            elif step.action == ActionType.SET_RANGE:
                value = str(step.value or "")
                await locator.evaluate(
                    """(element, nextValue) => {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                        setter.call(element, nextValue);
                        element.dispatchEvent(new Event('input', { bubbles: true }));
                        element.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    value,
                )
                actual = await locator.input_value(timeout=timeout)
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED" if actual == value else "FAILED",
                    "details": f"Set range value to {value}; observed {actual}",
                    "expected": value,
                    "actual": actual,
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
                # The wait length can be given as `value` or as `timeout_ms` (both in ms).
                if step.value and str(step.value).isdigit():
                    wait_ms = int(step.value)
                elif "timeout_ms" in step.model_fields_set and step.timeout_ms:
                    wait_ms = int(step.timeout_ms)
                else:
                    wait_ms = 1000
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

            elif step.action == ActionType.RELOAD_PAGE:
                response = await page.reload(timeout=timeout, wait_until="domcontentloaded")
                status_code = response.status if response else None
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "details": f"Page reloaded (HTTP {status_code})" if status_code else "Page reloaded",
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
