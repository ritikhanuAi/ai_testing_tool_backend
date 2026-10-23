from playwright.async_api import Page
from test_engine.schema.assertions import Assertion, AssertionType
from typing import Dict, Any

class AssertionRunner:
    """Evaluates deterministic assertions against current browser page state."""

    @staticmethod
    async def evaluate_assertion(page: Page, assertion: Assertion) -> Dict[str, Any]:
        """Check whether page state satisfies assertion condition."""
        try:
            a_type = assertion.type

            if a_type == AssertionType.ELEMENT_VISIBLE:
                locator = page.locator(assertion.target)
                is_visible = await locator.is_visible()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": is_visible,
                    "expected": "visible",
                    "actual": "visible" if is_visible else "not visible"
                }

            elif a_type == AssertionType.ELEMENT_HIDDEN:
                locator = page.locator(assertion.target)
                is_hidden = await locator.is_hidden()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": is_hidden,
                    "expected": "hidden",
                    "actual": "hidden" if is_hidden else "visible"
                }

            elif a_type == AssertionType.TEXT_CONTAINS:
                locator = page.locator(assertion.target)
                actual_text = await locator.inner_text()
                passed = str(assertion.value) in actual_text
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": passed,
                    "expected": f"contains '{assertion.value}'",
                    "actual": actual_text.strip()
                }

            elif a_type == AssertionType.OPTION_LIST_NOT_EMPTY:
                locator = page.locator(f"{assertion.target} option")
                count = await locator.count()
                return {
                    "type": a_type.value,
                    "target": assertion.target,
                    "passed": count > 0,
                    "expected": "> 0 options",
                    "actual": f"{count} options found"
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
