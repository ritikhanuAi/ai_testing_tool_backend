import time
from playwright.async_api import Page
from test_engine.schema.actions import ActionStep, ActionType
from typing import Dict, Any

class ActionRunner:
    """Executes discrete browser actions via Playwright and tracks timing and outcomes."""

    @staticmethod
    async def execute_step(page: Page, step: ActionStep) -> Dict[str, Any]:
        """Execute a single browser action step deterministically."""
        start_time = time.time()
        timeout = step.timeout_ms or 10000

        try:
            if step.action == ActionType.OPEN_PAGE:
                response = await page.goto(step.value, timeout=timeout, wait_until="domcontentloaded")
                status_code = response.status if response else 200
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": f"Page navigated successfully (HTTP {status_code})"
                }

            elif step.action == ActionType.CLICK:
                await page.click(step.target, timeout=timeout)
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": f"Clicked target '{step.target}'"
                }

            elif step.action == ActionType.TYPE_TEXT:
                await page.fill(step.target, str(step.value or ""), timeout=timeout)
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": f"Typed text into '{step.target}'"
                }

            elif step.action == ActionType.SELECT_OPTION:
                try:
                    await page.select_option(step.target, label=str(step.value), timeout=timeout)
                except Exception:
                    await page.select_option(step.target, value=str(step.value), timeout=timeout)
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": f"Selected option '{step.value}'"
                }

            elif step.action == ActionType.WAIT:
                wait_ms = int(step.value) if step.value and str(step.value).isdigit() else 1000
                await page.wait_for_timeout(wait_ms)
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": f"Waited {wait_ms}ms"
                }

            elif step.action == ActionType.HOVER:
                await page.hover(step.target, timeout=timeout)
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": f"Hovered over '{step.target}'"
                }

            elif step.action == ActionType.SCROLL:
                await page.evaluate("window.scrollBy(0, 500)")
                duration = time.time() - start_time
                return {
                    "action": step.action.value,
                    "target": step.target,
                    "status": "PASSED",
                    "duration_sec": round(duration, 3),
                    "details": "Scrolled down 500px"
                }

            else:
                return {
                    "action": step.action.value,
                    "status": "SKIPPED",
                    "duration_sec": 0.0,
                    "details": f"Action {step.action} not yet implemented"
                }

        except Exception as e:
            duration = time.time() - start_time
            return {
                "action": step.action.value,
                "target": step.target,
                "status": "FAILED",
                "duration_sec": round(duration, 3),
                "error": str(e)
            }
