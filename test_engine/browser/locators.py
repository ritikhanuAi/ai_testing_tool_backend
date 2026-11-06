import json
import re


_ROLE_TARGET = re.compile(r"^role=(?P<role>[a-zA-Z][\w-]*)(?:\[name=(?P<name>.+)\])?$")

# ALL_CAPS_WITH_UNDERSCORES (e.g. VERIFY_REQUIRED_EMAIL_INPUT) is a fill-in-later placeholder,
# never a real selector: HTML has no such element names. Failing at once avoids a long wait.
_PLACEHOLDER_TARGET = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

# XPath expressions: leading // (abbreviated syntax) or explicit xpath= prefix.
_XPATH_TARGET = re.compile(r"^(xpath=.+|//.+)", re.DOTALL)


class PlaceholderTargetError(ValueError):
    """Raised when a test step or assertion still uses a placeholder instead of a selector."""


def is_placeholder_target(target: str) -> bool:
    return bool(target) and bool(_PLACEHOLDER_TARGET.fullmatch(target.strip()))


def resolve_locator(page, target: str):
    """Resolve a test-pack selector to a Playwright locator.

    Supported selector forms:
    - CSS:   ``#email``, ``input[type='password']``, ``[data-testid='btn']``
    - Role:  ``role=button[name='Submit']``, ``role=textbox``
    - XPath: ``//input[@id='email']``, ``xpath=//div[@class='modal']``
    """
    if is_placeholder_target(target):
        raise PlaceholderTargetError(
            f"'{target.strip()}' is a placeholder, not a real selector. Replace it with a CSS "
            "selector (e.g. #email, input[type='password']), a role "
            "(e.g. role=button[name='Sign in']), or an XPath expression "
            "(e.g. //input[@id='email'])."
        )

    stripped = target.strip()

    if _XPATH_TARGET.match(stripped):
        # Normalise to explicit xpath= prefix so Playwright's engine selection is unambiguous.
        expr = stripped if stripped.startswith("xpath=") else f"xpath={stripped}"
        return page.locator(expr)

    match = _ROLE_TARGET.fullmatch(stripped)
    if not match:
        return page.locator(target)

    role = match.group("role")
    raw_name = match.group("name")
    if raw_name is None:
        return page.get_by_role(role)

    try:
        name = json.loads(raw_name)
    except json.JSONDecodeError:
        name = raw_name.strip("'\"")
    return page.get_by_role(role, name=name, exact=True)

