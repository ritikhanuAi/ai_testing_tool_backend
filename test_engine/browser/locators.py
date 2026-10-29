import json
import re


_ROLE_TARGET = re.compile(r"^role=(?P<role>[a-zA-Z][\w-]*)(?:\[name=(?P<name>.+)\])?$")


def resolve_locator(page, target: str):
    """Resolve the compact role syntax used in test packs, or a normal Playwright selector."""
    match = _ROLE_TARGET.fullmatch(target.strip())
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

