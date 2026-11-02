"""New test case vocabulary: pillar aliases, reload_page, wait timeout_ms, and five new assertions."""
import pytest
from pydantic import ValidationError

from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.assertion_runner import AssertionRunner
from test_engine.schema.actions import ActionStep
from test_engine.schema.assertions import Assertion
from test_engine.schema.case import TestCaseModel

LOGIN_PAGE = """
<html><body>
  <h1>Welcome back</h1>
  <input id="password" type="password" placeholder="Enter your password">
  <button id="eye" onclick="document.getElementById('password').type='text'">eye</button>
  <div id="error">Invalid email or password</div>
  <span id="count">0</span>
</body></html>
"""


def _case(**overrides):
    data = {
        "id": "TC-1",
        "title": "t",
        "steps": [{"action": "open_page", "value": "https://example.com"}],
        "assertions": [{"type": "element_visible", "target": "h1"}],
    }
    data.update(overrides)
    return TestCaseModel.model_validate(data)


@pytest.mark.parametrize("name, expected", [
    ("functionality", "functional"),
    ("user_friendly_ui", "accessibility"),
    ("Security", "security"),
    ("performance", "performance"),
])
def test_pillar_aliases(name, expected):
    assert _case(pillar=name).pillar.value == expected


def test_unknown_pillar_still_rejected():
    with pytest.raises(ValidationError):
        _case(pillar="marketing")


@pytest.mark.parametrize("assertion", [
    {"type": "attribute_equals", "target": "#p", "value": "text"},            # missing attribute
    {"type": "text_not_contains", "target": "#e"},                           # missing value
    {"type": "page_load_time_less_than", "value": "fast"},                   # not a number
    {"type": "response_time_less_than", "value": -5},                        # not positive
])
def test_new_assertions_reject_bad_fields(assertion):
    with pytest.raises(ValidationError):
        Assertion.model_validate(assertion)


@pytest.fixture
async def page():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.set_content(LOGIN_PAGE)
        yield pg
        await browser.close()


async def _check(page, **assertion):
    return await AssertionRunner.evaluate_assertion(page, Assertion.model_validate(assertion))


async def test_attribute_equals_and_text_not_contains(page):
    assert (await _check(page, type="attribute_equals", target="#password", attribute="type", value="password"))["passed"]
    assert (await _check(page, type="attribute_equals", target="#password", attribute="placeholder",
                         value="Enter your password"))["passed"]
    await page.click("#eye")
    assert (await _check(page, type="attribute_equals", target="#password", attribute="type", value="text"))["passed"]

    assert (await _check(page, type="text_not_contains", target="#error", value="Secret123"))["passed"]
    assert not (await _check(page, type="text_not_contains", target="#error", value="Invalid"))["passed"]
    # A missing element is a failure, not a silent pass.
    missing = await _check(page, type="text_not_contains", target="#does-not-exist", value="x")
    assert not missing["passed"]


async def test_timing_assertions(page):
    generous = await _check(page, type="page_load_time_less_than", value=60000)
    assert generous["passed"], generous
    assert not (await _check(page, type="page_load_time_less_than", value=0.001))["passed"]

    render = await _check(page, type="render_time_less_than", value=60000)
    assert render["passed"], render

    fast = await AssertionRunner.evaluate_assertion(
        page, Assertion(type="response_time_less_than", value=3000), response_time_ms=1200
    )
    slow = await AssertionRunner.evaluate_assertion(
        page, Assertion(type="response_time_less_than", value=3000), response_time_ms=4500
    )
    unmeasured = await AssertionRunner.evaluate_assertion(page, Assertion(type="response_time_less_than", value=3000))
    assert fast["passed"] and not slow["passed"] and not unmeasured["passed"]


async def test_reload_page_and_wait_uses_timeout_ms(page):
    await page.goto("data:text/html,<h1>hello</h1>")
    reload_result = await ActionRunner.execute_step(page, ActionStep(action="reload_page"))
    assert reload_result["status"] == "PASSED", reload_result

    wait_result = await ActionRunner.execute_step(page, ActionStep(action="wait", timeout_ms=1500))
    assert wait_result["details"] == "Waited 1500ms"
    default_wait = await ActionRunner.execute_step(page, ActionStep(action="wait"))
    assert default_wait["details"] == "Waited 1000ms"


@pytest.mark.parametrize("target, expected", [
    ("VERIFY_REQUIRED_EMAIL_INPUT", True),
    ("LOGIN_FORM", True),
    ("#email", False),
    ("h1", False),
    ("H1", False),
    ("role=button[name='Sign in']", False),
    ("input[name='MY_FIELD']", False),
])
def test_placeholder_detection(target, expected):
    from test_engine.browser.locators import is_placeholder_target
    assert is_placeholder_target(target) is expected


async def test_placeholder_targets_fail_immediately_with_clear_message(page):
    import time
    started = time.perf_counter()
    step = await ActionRunner.execute_step(page, ActionStep(action="click", target="VERIFY_REQUIRED_SIGN_IN_BUTTON"))
    check = await _check(page, type="text_contains", target="VERIFY_REQUIRED_PAGE_HEADING", value="Welcome")
    assert time.perf_counter() - started < 2
    assert step["status"] == "FAILED" and "placeholder" in step["error"]
    assert not check["passed"] and "placeholder" in check["error"]


async def test_text_check_on_missing_element_waits_at_most_a_few_seconds(page):
    import time
    started = time.perf_counter()
    result = await _check(page, type="text_contains", target="#not-on-page", value="x")
    assert not result["passed"]
    assert time.perf_counter() - started < 8  # was ~30 s before
