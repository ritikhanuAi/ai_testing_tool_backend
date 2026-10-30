from playwright.async_api import Page
from test_engine.schema.assertions import Assertion, AssertionType
from test_engine.browser.locators import resolve_locator
from typing import Dict, Any, List, Optional

class AssertionRunner:
    """Evaluates deterministic assertions against current browser page state."""

    @staticmethod
    async def _read_text(locator) -> str:
        tag = await locator.evaluate("element => element.tagName.toLowerCase()")
        if tag in ("input", "textarea", "select"):
            return await locator.input_value()
        return await locator.inner_text()

    @staticmethod
    async def evaluate_assertion(
        page: Page,
        assertion: Assertion,
        last_status_code: Optional[int] = None,
        server_error_statuses: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Check whether page state satisfies assertion condition."""
        try:
            a_type = assertion.type

            if a_type == AssertionType.ELEMENT_VISIBLE:
                locator = resolve_locator(page, assertion.target)
                is_visible = await locator.is_visible()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": is_visible,
                    "expected": "visible",
                    "actual": "visible" if is_visible else "not visible"
                }

            elif a_type == AssertionType.ELEMENT_HIDDEN:
                locator = resolve_locator(page, assertion.target)
                is_hidden = await locator.is_hidden()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": is_hidden,
                    "expected": "hidden",
                    "actual": "hidden" if is_hidden else "visible"
                }

            elif a_type == AssertionType.TEXT_CONTAINS:
                locator = resolve_locator(page, assertion.target)
                actual_text = await AssertionRunner._read_text(locator)
                passed = str(assertion.value) in actual_text
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": passed,
                    "expected": f"contains '{assertion.value}'",
                    "actual": actual_text.strip()
                }

            elif a_type == AssertionType.TEXT_EQUALS:
                locator = resolve_locator(page, assertion.target)
                actual_text = (await AssertionRunner._read_text(locator)).strip()
                expected_text = str(assertion.value).strip()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": actual_text == expected_text,
                    "expected": expected_text,
                    "actual": actual_text,
                }

            elif a_type == AssertionType.OPTION_LIST_NOT_EMPTY:
                locator = resolve_locator(page, assertion.target).locator("option")
                count = await locator.count()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": count > 0,
                    "expected": "> 0 options",
                    "actual": f"{count} options found"
                }

            elif a_type == AssertionType.OPTION_COUNT_EQUALS:
                count = await resolve_locator(page, assertion.target).locator("option").count()
                expected_count = int(assertion.value)
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": count == expected_count,
                    "expected": expected_count,
                    "actual": count,
                }

            elif a_type == AssertionType.STATUS_CODE_EQUALS:
                actual_status = last_status_code
                if actual_status is None:
                    actual_status = await page.evaluate(
                        "() => performance.getEntriesByType('navigation')[0]?.responseStatus ?? null"
                    )
                expected_status = int(assertion.value)
                return {
                    "type": a_type.value,
                    "passed": actual_status == expected_status,
                    "expected": expected_status,
                    "actual": actual_status,
                }

            elif a_type == AssertionType.URL_CONTAINS:
                current_url = page.url
                passed = str(assertion.value) in current_url
                return {
                    "type": a_type.value,
                    "passed": passed,
                    "expected": f"URL contains '{assertion.value}'",
                    "actual": current_url
                }

            elif a_type == AssertionType.PAGE_TITLE_CONTAINS:
                title = await page.title()
                passed = str(assertion.value).lower() in title.lower()
                return {
                    "type": a_type.value,
                    "passed": passed,
                    "expected": f"title contains '{assertion.value}'",
                    "actual": title
                }

            elif a_type == AssertionType.INPUT_INVALID:
                is_invalid = await resolve_locator(page, assertion.target).evaluate(
                    "element => element.checkValidity() === false"
                )
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": is_invalid,
                    "expected": "input reports invalid value",
                    "actual": "invalid" if is_invalid else "valid",
                }

            elif a_type == AssertionType.FORM_INVALID:
                is_invalid = await resolve_locator(page, assertion.target).evaluate(
                    "form => form.checkValidity() === false"
                )
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": is_invalid,
                    "expected": "form reports invalid input",
                    "actual": "invalid" if is_invalid else "valid",
                }

            elif a_type == AssertionType.PAGE_NO_SERVER_ERROR:
                body_text = (await page.locator("body").inner_text()).lower()
                error_markers = (
                    "internal server error",
                    "500 internal server error",
                    "application error",
                    "service unavailable",
                    "bad gateway",
                )
                actual_status = last_status_code
                if actual_status is None:
                    actual_status = await page.evaluate(
                        "() => performance.getEntriesByType('navigation')[0]?.responseStatus ?? null"
                    )
                has_server_error = any(marker in body_text for marker in error_markers)
                failed_responses = [status for status in (server_error_statuses or []) if status >= 500]
                status_ok = (actual_status is None or actual_status < 500) and not failed_responses
                return {
                    "type": a_type.value,
                    "passed": status_ok and not has_server_error,
                    "expected": "no server error response or error page",
                    "actual": f"navigation HTTP {actual_status}; failed responses={failed_responses}; error text={has_server_error}",
                }

            else:
                return {
                    "type": a_type.value,
                    "passed": False,
                    "error": f"Assertion type '{a_type}' not yet implemented"
                }

        except Exception as e:
            return {
                "type": assertion.type.value,
                "target": assertion.target,
                "passed": False,
                "error": str(e)
            }
