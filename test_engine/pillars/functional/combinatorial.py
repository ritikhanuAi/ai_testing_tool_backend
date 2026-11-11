"""Deterministic finite-domain interaction coverage for the current page.

The suite can enumerate the full Cartesian product or a deterministic pairwise
covering set of discoverable safe control values. Text inputs have an explicit
finite domain supplied by the scan options. Action-order permutations are opt-in
and stay within each configured priority tier.
"""

import asyncio
import hashlib
import itertools
import json
import math
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import Page

from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.evidence import EvidenceCollector
from test_engine.browser.readiness import wait_for_page_ready
from test_engine.crawl.site_crawler import redact_text, redact_url
from test_engine.pillars.functional.spider import FunctionalSpider
from test_engine.schema.actions import ActionStep, ActionType


DEFAULT_INTERACTION_PRIORITY_ORDER = ("dropdown", "search", "navigation", "button")
INTERACTION_CASE_ORDER_VERSION = 3
MAX_PAIRWISE_REQUIREMENTS = 250_000


def _same_origin_http_route(candidate: str, base_url: str) -> Optional[str]:
    """Return a normalized same-origin HTTP route, excluding fragments and credentials."""
    try:
        absolute = urljoin(base_url, str(candidate).strip())
        parsed = urlsplit(absolute)
        base = urlsplit(base_url)
        parsed_port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        base_port = base.port or (443 if base.scheme.lower() == "https" else 80)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.hostname != base.hostname
            or parsed_port != base_port
            or parsed.username
            or parsed.password
        ):
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    except (TypeError, ValueError):
        return None


def _factor_priority_kind(factor: Dict[str, Any]) -> str:
    kind = str(factor.get("kind") or "")
    if kind in {"native_select", "custom_dropdown", "custom_dropdown_without_exposed_options"}:
        return "dropdown"
    if kind == "navigation_menu":
        return "navigation"
    label = str(factor.get("label") or "").lower()
    if kind == "search_input" or "search" in label or "filter" in label:
        return "search"
    if kind == "safe_button":
        return "button"
    return "other"


def factor_category(factor: Dict[str, Any]) -> str:
    """Map a discovered factor into the user-selectable category list."""
    priority_kind = _factor_priority_kind(factor)
    return priority_kind if priority_kind != "other" else "form"


def _ordered_priority_categories(priority_order: Optional[List[str]] = None) -> List[str]:
    order = [category for category in (priority_order or DEFAULT_INTERACTION_PRIORITY_ORDER)
             if category in DEFAULT_INTERACTION_PRIORITY_ORDER]
    order.extend(category for category in DEFAULT_INTERACTION_PRIORITY_ORDER if category not in order)
    return [*order, "other"]


def _is_safe_discovery_trigger(item: Dict[str, Any]) -> bool:
    name = str(item.get("name") or "").strip().lower()
    trigger_words = (
        "search", "filter", "menu", "more", "sort", "expand", "collapse", "view", "open",
        "select", "choose", "dropdown", "status", "category", "severity",
        "road type", "page size", "per page",
    )
    expanded = str(item.get("expanded") or "").lower()
    return (
        str(item.get("role") or "").lower() == "button"
        and "click" in set(item.get("allowed_actions") or [])
        and expanded != "true"
        and bool(
            item.get("has_popup")
            or expanded == "false"
            or item.get("controls")
            or any(word in name for word in trigger_words)
        )
    )


async def _click_discovered_control(page: Page, item: Dict[str, Any], timeout_ms: int) -> None:
    """Click a discovered control by accessible name, with its CSS path as fallback."""
    role = str(item.get("fallback_role") or item.get("role") or "").strip()
    name = str(item.get("fallback_name") or item.get("name") or "").strip()
    errors: List[Exception] = []

    if role and name:
        try:
            candidates = page.get_by_role(role, name=name, exact=True)
            count = await candidates.count()
            visible = []
            for index in range(min(count, 20)):
                candidate = candidates.nth(index)
                if await candidate.is_visible():
                    visible.append(candidate)
            if len(visible) == 1:
                await visible[0].click(timeout=timeout_ms)
                return
        except Exception as error:
            errors.append(error)

    selector = str(item.get("selector") or "").strip()
    if selector:
        try:
            candidates = page.locator(selector)
            if await candidates.count() == 1 and await candidates.is_visible():
                await candidates.click(timeout=timeout_ms)
                return
        except Exception as error:
            errors.append(error)

    if errors:
        raise errors[-1]
    description = f"role={role!r}, name={name!r}" if role and name else f"selector={selector!r}"
    raise TimeoutError(f"Could not uniquely locate a visible discovered control ({description}).")


def _prioritize_factors(
    factors: List[Dict[str, Any]], priority_order: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Order case dimensions so first-priority controls are exercised first."""
    order = list(priority_order or DEFAULT_INTERACTION_PRIORITY_ORDER)
    for category in DEFAULT_INTERACTION_PRIORITY_ORDER:
        if category not in order:
            order.append(category)
    rank = {category: index for index, category in enumerate(order)}
    ranked = sorted(
        enumerate(factors),
        key=lambda indexed: (
            rank.get(_factor_priority_kind(indexed[1]), len(rank)), indexed[0]
        ),
    )
    prioritized = [factor for _, factor in ranked]
    for factor_index, factor in enumerate(prioritized, start=1):
        factor["id"] = f"factor-{factor_index}"
        category = _factor_priority_kind(factor)
        # Try prioritized interactions first. For secondary controls, hold the
        # discovered initial state while the P1 combinations run.
        factor["values"] = sorted(
            factor.get("values") or [],
            key=lambda value: (
                value.get("action") is None if category != "other" else value.get("action") is not None
            ),
        )
    return prioritized


def _unique_strings(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        value = str(value)
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _group_equivalent_safe_buttons(factors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Represent repeated same-name buttons as one choice factor.

    Repeated row actions (for example, one `View on map` button per record) are
    alternatives in one case. Treating every row button as an independent
    on/off factor creates 2^N combinations of simultaneous row actions, which
    usually cannot be performed together and overwhelms coverage with duplicates.
    """
    grouped: Dict[tuple, Dict[str, Any]] = {}
    result: List[Dict[str, Any]] = []
    for factor in factors:
        if factor.get("kind") != "safe_button":
            result.append(factor)
            continue
        normalized_label = " ".join(str(factor.get("label") or "").casefold().split())
        setup_key = tuple(
            str(action.get("target") or "")
            for action in factor.get("setup_actions") or []
        )
        key = (normalized_label, setup_key)
        if not key:
            result.append(factor)
            continue
        existing = grouped.get(key)
        if existing is None:
            copied = {**factor, "values": [value for value in factor.get("values", []) if value.get("action") is None]}
            copied["_button_targets"] = set()
            grouped[key] = copied
            result.append(copied)
            existing = copied
        for value in factor.get("values", []):
            if value.get("action") is None:
                continue
            target = str(value.get("target") or "")
            if target in existing["_button_targets"]:
                continue
            existing["_button_targets"].add(target)
            ordinal = len(existing["_button_targets"])
            merged_value = {**value}
            context = str(value.get("context_name") or "").strip()
            label = f"click {context}" if context else f"click control {ordinal}"
            used_labels = {str(item.get("label") or "") for item in existing["values"]}
            if label in used_labels:
                label = f"{label} #{ordinal}"
            merged_value["label"] = label
            existing["values"].append(merged_value)

    for factor in result:
        targets = factor.pop("_button_targets", None)
        if targets and len(targets) > 1:
            factor["label"] = f"{factor['label']} ({len(targets)} similar controls)"
    # Keep factor IDs compact and deterministic after collapsing duplicates.
    for index, factor in enumerate(result, start=1):
        factor["id"] = f"factor-{index}"
    return result


def _factor_value(
    *, label: str, action: Optional[ActionType], target: str, value: Optional[str], item: Dict[str, Any],
    select_by_value: bool = False,
) -> Dict[str, Any]:
    return {
        "label": label,
        "action": action.value if action else None,
        "target": target,
        "value": value,
        "fallback_role": item.get("fallback_role"),
        "fallback_name": item.get("fallback_name"),
        "context_name": redact_text(str(item.get("context_name") or "")),
        "select_by_value": select_by_value,
        "has_popup": item.get("has_popup"),
    }


async def _control_surface_fingerprint(page: Page) -> str:
    """Hash visible UI state without retaining page text or user data in the report."""
    try:
        return await page.evaluate("""() => {
            const visible = element => {
                const style = getComputedStyle(element);
                return style.display !== 'none' && style.visibility !== 'hidden' && element.getClientRects().length > 0;
            };
            const name = element => (element.getAttribute('aria-label') || element.innerText ||
                element.getAttribute('placeholder') || element.getAttribute('title') || '')
                .trim().replace(/\\s+/g, ' ').slice(0, 100);
            const controls = Array.from(document.querySelectorAll(
                'button, input, select, textarea, [role], [tabindex], [aria-label]'
            )).filter(visible).map(element => [
                element.tagName.toLowerCase(), element.getAttribute('role') || '', name(element),
                element.getAttribute('aria-expanded') || '', element.getAttribute('aria-selected') || ''
            ]);
            const text = (document.body?.innerText || '').slice(0, 20000);
            let hash = 2166136261;
            for (let index = 0; index < text.length; index++) {
                hash = Math.imul(hash ^ text.charCodeAt(index), 16777619);
            }
            return JSON.stringify({controls, textLength: text.length, textHash: hash >>> 0});
        }""")
    except Exception:
        return ""


async def _is_disclosure_open(
    page: Page,
    setup_action: Dict[str, Any],
    expected_selector: Optional[str] = None,
) -> bool:
    """Avoid toggling an already-open parent menu while reaching a revealed control."""
    try:
        trigger = page.locator(str(setup_action.get("target") or ""))
        expanded = bool(
            await trigger.count()
            and (await trigger.get_attribute("aria-expanded") or "").lower() == "true"
        )
        controls = (await trigger.get_attribute("aria-controls") or "").split()
        controlled_popup_visible = False
        for control_id in controls:
            popup = page.locator(f"[id={json.dumps(control_id)}]")
            if await popup.count() and await popup.first.is_visible():
                controlled_popup_visible = True
                break
        if expected_selector:
            expected = page.locator(expected_selector)
            try:
                await expected.first.wait_for(state="visible", timeout=500)
                return True
            except Exception:
                return False
        return expanded or controlled_popup_visible
    except Exception:
        return False


async def discover_interaction_factors(
    page: Page,
    *,
    target_url: str,
    text_values: List[str],
    priority_order: Optional[List[str]] = None,
    included_categories: Optional[List[str]] = None,
    deadline: Optional[float] = None,
    cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """Discover safe finite control domains, opening custom listboxes to read options."""
    catalog = await FunctionalSpider.extract_interactive_catalog(page)
    factors: List[Dict[str, Any]] = []
    consumed_groups = set()
    undiscovered_custom_dropdowns = 0
    discovery_errors: List[str] = []
    unsupported_controls: List[Dict[str, str]] = []
    discovery_complete = True
    original_url = page.url
    expanded_catalog = list(catalog)
    dynamic_discovered: List[Dict[str, str]] = []
    linked_routes: List[str] = []

    def record_linked_route(href: Optional[str]) -> None:
        if not href:
            return
        normalized = _same_origin_http_route(str(href), original_url)
        if not normalized:
            return
        base = _same_origin_http_route(original_url, original_url)
        if normalized != base and normalized not in linked_routes:
            linked_routes.append(normalized)

    def retain_open_menu_controls(items: List[Dict[str, Any]]) -> None:
        for opened_item in items:
            opened_role = str(opened_item.get("role") or "").lower()
            if opened_role not in {"option", "menuitem", "link"} and not opened_item.get("href"):
                continue
            opened_selector = str(opened_item.get("selector") or "")
            if opened_selector and not any(
                str(existing.get("selector") or "") == opened_selector for existing in expanded_catalog
            ):
                expanded_catalog.append(opened_item)

    def add_safe_menu_actions(
        trigger: Dict[str, Any],
        menu_items: List[Dict[str, Any]],
        setup_actions: List[Dict[str, Any]],
    ) -> int:
        added = 0
        trigger_selector = str(trigger.get("selector") or "")
        if not trigger_selector:
            return added
        trigger_action = {
            "action": ActionType.CLICK.value,
            "target": trigger_selector,
            "fallback_role": trigger.get("fallback_role"),
            "fallback_name": trigger.get("fallback_name"),
            "label": redact_text(str(trigger.get("name") or "open menu")),
        }
        full_setup = [dict(action) for action in setup_actions]
        if not any(str(action.get("target") or "") == trigger_selector for action in full_setup):
            full_setup.append(trigger_action)
        for menu_item in menu_items:
            if str(menu_item.get("role") or "").lower() != "menuitem":
                continue
            if str(menu_item.get("tag") or "").lower() == "a" or menu_item.get("href"):
                continue
            if "click" not in set(menu_item.get("allowed_actions") or []):
                continue
            selector = str(menu_item.get("selector") or "")
            name = str(menu_item.get("name") or "").strip()
            if not selector or not name or any(
                word in name.lower()
                for word in ("delete", "remove", "logout", "log out", "purchase", "checkout", "pay", "submit")
            ):
                continue
            context = str(menu_item.get("context_name") or "").strip()
            label = f"{name} ({context})" if context else name
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(label),
                "kind": "safe_button",
                "values": [
                    _factor_value(label="not clicked", action=None, target=selector, value=None, item=menu_item),
                    _factor_value(label="clicked", action=ActionType.CLICK, target=selector, value=None, item=menu_item),
                ],
                "setup_actions": full_setup,
            })
            added += 1
        return added

    def factor_domain_exists(kind: str, label: str, values: List[Dict[str, Any]]) -> bool:
        def signature(domain: List[Dict[str, Any]]) -> List[tuple[str, str, str, str]]:
            return sorted((
                str(value.get("action") or ""),
                str(value.get("target") or ""),
                str(value.get("label") or ""),
                str(value.get("value") or ""),
            ) for value in domain)

        expected = signature(values)
        normalized_label = " ".join(label.casefold().split())
        return any(
            factor.get("kind") == kind
            and " ".join(str(factor.get("label") or "").casefold().split()) == normalized_label
            and signature(factor.get("values") or []) == expected
            for factor in factors
        )

    for item_index, item in enumerate(catalog):
        if cancellation_check and await cancellation_check():
            raise asyncio.CancelledError("Scan cancelled during interaction-control discovery")
        if deadline is not None and time.monotonic() >= deadline:
            discovery_complete = False
            discovery_errors.append("The page interaction time budget ended while discovering controls.")
            break
        selector = item.get("selector")
        role = str(item.get("role") or "").lower()
        tag = str(item.get("tag") or "").lower()
        kind = str(item.get("type") or "").lower()
        name = str(item.get("name") or item.get("placeholder") or f"{role} {item_index + 1}").strip()
        actions = set(item.get("allowed_actions") or [])
        if not selector:
            continue

        if role in {"radio", "tab"}:
            group_key = item.get("group_key") or f"{role}:{selector}"
            if group_key in consumed_groups:
                continue
            consumed_groups.add(group_key)
            group_items = [
                candidate for candidate in catalog
                if candidate.get("group_key") == group_key and candidate.get("selector")
            ]
            if not group_items:
                group_items = [item]
            values = []
            for option_index, option in enumerate(group_items, start=1):
                option_name = str(option.get("name") or option.get("value") or f"option {option_index}").strip()
                is_selected = (
                    option.get("checked") is True
                    if role == "radio"
                    else option.get("selected") is True
                )
                values.append(_factor_value(
                    label=option_name,
                    action=None if is_selected else ActionType.CLICK,
                    target=option["selector"],
                    value=None,
                    item=option,
                ))
            if values:
                factors.append({
                    "id": f"factor-{len(factors) + 1}",
                    "label": redact_text(name),
                    "kind": "radio_group" if role == "radio" else "tab_group",
                    "values": values,
                })
            continue

        if role in {"checkbox", "switch"} and "click" in actions:
            current_checked = item.get("checked")
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "checkbox",
                "values": [
                    _factor_value(label="unchecked", action=None if current_checked is False else ActionType.SET_CHECKED, target=selector, value="false", item=item),
                    _factor_value(label="checked", action=None if current_checked is True else ActionType.SET_CHECKED, target=selector, value="true", item=item),
                ],
            })
            continue

        if role == "slider" and "set_range" in actions:
            try:
                minimum = Decimal(str(item.get("range_min") or "0"))
                maximum = Decimal(str(item.get("range_max") or "100"))
                raw_step = str(item.get("range_step") or "1")
                if raw_step.lower() == "any":
                    raise ValueError("the range uses step=any and has no finite discrete domain")
                step_size = Decimal(raw_step)
                if step_size <= 0 or maximum < minimum:
                    raise ValueError("invalid min, max, or step")
                count = int((maximum - minimum) // step_size) + 1
                if count > 10000:
                    raise ValueError(f"the range has {count} values, above the 10000-value discovery bound")
                values = []
                for value_index in range(count):
                    raw_value = minimum + (step_size * value_index)
                    formatted = format(raw_value.normalize(), "f")
                    current_value = str(item.get("range_value") or "")
                    values.append(_factor_value(
                        label=formatted,
                        action=None if current_value == formatted else ActionType.SET_RANGE,
                        target=selector,
                        value=formatted,
                        item=item,
                    ))
                factors.append({
                    "id": f"factor-{len(factors) + 1}",
                    "label": redact_text(name),
                    "kind": "slider",
                    "values": values,
                })
            except (InvalidOperation, ValueError, ZeroDivisionError) as error:
                unsupported_controls.append({"label": redact_text(name), "reason": str(error)})
            continue

        if role == "button" and str(item.get("has_popup") or "").lower() != "menu" and "click" in actions and any(
            word in name.lower()
            for word in ("search", "filter", "clear", "reset", "apply", "sort", "expand", "collapse", "menu", "view", "open")
        ):
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "safe_button",
                "values": [
                    _factor_value(label="not clicked", action=None, target=selector, value=None, item=item),
                    _factor_value(label="clicked", action=ActionType.CLICK, target=selector, value=None, item=item),
                ],
            })
            continue

        if role == "combobox" and tag == "select" and "select_option" in actions:
            values = []
            for option in item.get("options") or []:
                if option.get("disabled"):
                    continue
                option_value = str(option.get("value") or "")
                option_label = str(option.get("label") or option_value or "(empty)")
                values.append(_factor_value(
                    label=option_label,
                    action=ActionType.SELECT_OPTION,
                    target=selector,
                    value=option_value,
                    item=item,
                    select_by_value=True,
                ))
            if values:
                factors.append({
                    "id": f"factor-{len(factors) + 1}",
                    "label": redact_text(name),
                    "kind": "native_select",
                    "values": values,
                })
            continue

        if role == "combobox" and tag != "select" and "click" in actions:
            option_names: List[str] = []
            opened_menu_catalog: List[Dict[str, Any]] = []
            links_before_menu = len(linked_routes)
            menu_actions_added = 0
            try:
                trigger = page.locator(selector)
                await trigger.click(timeout=2500)
                options = page.locator('[role="option"]:visible')
                try:
                    await options.first.wait_for(state="visible", timeout=2500)
                except Exception:
                    pass
                opened_menu_catalog = await FunctionalSpider.extract_interactive_catalog(page)
                for opened_item in opened_menu_catalog:
                    if opened_item.get("href"):
                        record_linked_route(str(opened_item["href"]))
                menu_actions_added = add_safe_menu_actions(item, opened_menu_catalog, [])
                total_option_count = await options.count()
                count = min(total_option_count, 200)
                if total_option_count > 200:
                    unsupported_controls.append({
                        "label": redact_text(name),
                        "reason": f"the dropdown exposes {total_option_count} options; only 200 can be inventoried in one scan",
                    })
                for option_index in range(count):
                    option = options.nth(option_index)
                    metadata = await option.evaluate(
                        "element => ({ tag: element.tagName.toLowerCase(), href: element.getAttribute('href') })"
                    )
                    if metadata.get("tag") == "a" or metadata.get("href"):
                        record_linked_route(metadata.get("href"))
                        continue
                    disabled = (await option.get_attribute("aria-disabled") or "").lower() == "true"
                    if disabled:
                        continue
                    option_name = (await option.get_attribute("aria-label")) or (await option.inner_text())
                    option_name = " ".join(str(option_name).split())
                    if option_name:
                        option_names.append(option_name)
                retain_open_menu_controls(opened_menu_catalog)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(50)
            except Exception as error:
                discovery_errors.append(f"Could not inspect custom dropdown {redact_text(name)}: {redact_text(str(error))[:180]}")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass

            option_names = _unique_strings(option_names)
            values = [
                _factor_value(
                    label=option_name,
                    action=ActionType.SELECT_OPTION,
                    target=selector,
                    value=option_name,
                    item=item,
                )
                for option_name in option_names
            ]
            if not values:
                navigation_menu = len(linked_routes) > links_before_menu
                if not navigation_menu and not menu_actions_added:
                    undiscovered_custom_dropdowns += 1
                values = [_factor_value(
                    label="open linked menu" if navigation_menu else "open dropdown",
                    action=ActionType.CLICK,
                    target=selector,
                    value=None,
                    item=item,
                )]
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "custom_dropdown" if option_names else "navigation_menu" if len(linked_routes) > links_before_menu else "custom_dropdown_without_exposed_options",
                "values": values,
            })
            if page.url != original_url:
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    await wait_for_page_ready(page, timeout_ms=5000)
                except Exception as error:
                    discovery_complete = False
                    discovery_errors.append(f"Dropdown inspection changed the selected page and could not restore it: {redact_text(str(error))[:180]}")
                    break
            continue

        if role == "button" and str(item.get("has_popup") or "").lower() == "menu" and "click" in actions:
            menu_names: List[str] = []
            opened_menu_catalog: List[Dict[str, Any]] = []
            links_before_menu = len(linked_routes)
            menu_actions_added = 0
            try:
                await page.locator(selector).click(timeout=2500)
                try:
                    await page.locator('[role="option"]:visible, [role="menuitem"]:visible').first.wait_for(
                        state="visible", timeout=2500
                    )
                except Exception:
                    pass
                opened_menu_catalog = await FunctionalSpider.extract_interactive_catalog(page)
                for opened_item in opened_menu_catalog:
                    if opened_item.get("href"):
                        record_linked_route(str(opened_item["href"]))
                menu_actions_added = add_safe_menu_actions(item, opened_menu_catalog, [])
                menu_items = page.locator('[role="option"]:visible')
                total_menu_count = await menu_items.count()
                if total_menu_count > 200:
                    unsupported_controls.append({
                        "label": redact_text(name),
                        "reason": f"the menu exposes {total_menu_count} options; only 200 are inventoried",
                    })
                for menu_index in range(min(total_menu_count, 200)):
                    menu_item = menu_items.nth(menu_index)
                    metadata = await menu_item.evaluate("element => ({ tag: element.tagName.toLowerCase(), href: element.getAttribute('href') })")
                    if metadata.get("tag") == "a" or metadata.get("href"):
                        record_linked_route(metadata.get("href"))
                        continue
                    menu_name = (await menu_item.get_attribute("aria-label")) or (await menu_item.inner_text())
                    menu_name = " ".join(str(menu_name).split())
                    if menu_name:
                        menu_names.append(menu_name)
                retain_open_menu_controls(opened_menu_catalog)
                await page.keyboard.press("Escape")
            except Exception as error:
                discovery_errors.append(f"Could not inspect menu dropdown {redact_text(name)}: {redact_text(str(error))[:180]}")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass

            menu_names = _unique_strings(menu_names)
            values = [
                _factor_value(
                    label=menu_name,
                    action=ActionType.SELECT_OPTION,
                    target=selector,
                    value=menu_name,
                    item=item,
                )
                for menu_name in menu_names
            ]
            if not values:
                navigation_menu = len(linked_routes) > links_before_menu
                if not navigation_menu and not menu_actions_added:
                    undiscovered_custom_dropdowns += 1
                values = [_factor_value(
                    label="open linked menu" if navigation_menu else "open dropdown",
                    action=ActionType.CLICK,
                    target=selector,
                    value=None,
                    item=item,
                )]
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "custom_dropdown" if menu_names else "navigation_menu" if len(linked_routes) > links_before_menu else "custom_dropdown_without_exposed_options",
                "values": values,
            })
            if page.url != original_url:
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    await wait_for_page_ready(page, timeout_ms=5000)
                except Exception as error:
                    discovery_complete = False
                    discovery_errors.append(f"Menu inspection changed the selected page and could not restore it: {redact_text(str(error))[:180]}")
                    break
            continue

        if role in {"textbox", "searchbox"} and "type_text" in actions:
            values = _unique_strings(["", *text_values])
            if values:
                factors.append({
                    "id": f"factor-{len(factors) + 1}",
                    "label": redact_text(name),
                    "kind": "search_input" if role == "searchbox" or kind == "search" or any(
                        word in name.lower() for word in ("search", "filter")
                    ) else "text_input",
                    "values": [
                        _factor_value(
                            label="(empty)" if value == "" else value,
                            action=ActionType.TYPE_TEXT,
                            target=selector,
                            value=value,
                            item=item,
                        )
                        for value in values
                    ],
                })

    # Open safe, explicit menu/search/filter triggers once during discovery.
    # This exposes controls that are absent from the page's initial DOM snapshot.
    initial_selectors = {str(item.get("selector") or "") for item in catalog}
    explored_triggers = set()
    trigger_queue = [(trigger, []) for trigger in catalog if _is_safe_discovery_trigger(trigger)]
    trigger_index = 0
    while trigger_index < len(trigger_queue):
        if cancellation_check and await cancellation_check():
            raise asyncio.CancelledError("Scan cancelled during staged interaction discovery")
        if deadline is not None and time.monotonic() >= deadline:
            discovery_errors.append("The page interaction time budget ended while opening controls for discovery.")
            break
        if trigger_index >= 40:
            unsupported_controls.append({
                "label": "Remaining safe disclosure triggers",
                "reason": "control discovery reached its 40-trigger safety bound before inspecting all candidates",
            })
            break
        trigger, parent_setup_actions = trigger_queue[trigger_index]
        trigger_index += 1
        trigger_selector = str(trigger.get("selector") or "")
        trigger_name = str(trigger.get("name") or "").strip()
        links_before_trigger = len(linked_routes)
        trigger_key = (trigger_selector, tuple(str(action.get("target") or "") for action in parent_setup_actions))
        if not trigger_selector or trigger_key in explored_triggers:
            continue
        explored_triggers.add(trigger_key)
        added_for_trigger = False
        try:
            parent_failed = False
            for parent_action in parent_setup_actions:
                parent_step = ActionStep(
                    action=ActionType.CLICK,
                    target=parent_action["target"],
                    fallback_role=parent_action.get("fallback_role"),
                    fallback_name=parent_action.get("fallback_name"),
                    timeout_ms=2500,
                    description=f"Open control required for {parent_action.get('label') or 'discovery'}",
                )
                parent_result = await ActionRunner.execute_step(page, parent_step)
                if parent_result.get("status") != "PASSED":
                    parent_failed = True
                    break
            if parent_failed:
                continue
            before_pages = set(page.context.pages)
            await _click_discovered_control(page, trigger, timeout_ms=2500)
            await page.wait_for_timeout(250)
            new_pages = [candidate for candidate in page.context.pages if candidate not in before_pages]
            for candidate in new_pages:
                try:
                    try:
                        await candidate.wait_for_load_state("domcontentloaded", timeout=1000)
                    except Exception:
                        pass
                    if candidate.url and candidate.url != "about:blank":
                        record_linked_route(candidate.url)
                    await candidate.close()
                except Exception:
                    pass
            current_route = _same_origin_http_route(page.url, original_url)
            original_route = _same_origin_http_route(original_url, original_url)
            same_selected_page = current_route == original_route
            if current_route and not same_selected_page:
                record_linked_route(current_route)
            if not same_selected_page or new_pages:
                if not same_selected_page:
                    await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                    await wait_for_page_ready(page, timeout_ms=5000)
                continue

            revealed_catalog = await FunctionalSpider.extract_interactive_catalog(page)
            for revealed_link in revealed_catalog:
                if str(revealed_link.get("href") or ""):
                    record_linked_route(str(revealed_link["href"]))
            setup_action = {
                "action": ActionType.CLICK.value,
                "target": trigger_selector,
                "fallback_role": trigger.get("fallback_role"),
                "fallback_name": trigger.get("fallback_name"),
                "label": redact_text(trigger_name or "open control"),
            }
            setup_actions = [dict(action) for action in parent_setup_actions] + [setup_action]
            revealed_menu_options = [
                option for option in revealed_catalog
                if str(option.get("role") or "").lower() == "option"
                and str(option.get("tag") or "").lower() != "a"
                and str(option.get("name") or "").strip()
            ]
            menu_names = _unique_strings([
                str(option.get("name") or "").strip()
                for option in revealed_menu_options
                if not any(
                    word in str(option.get("name") or "").lower()
                    for word in ("delete", "remove", "logout", "log out", "purchase", "checkout", "pay", "submit")
                )
            ])
            menu_actions_added = add_safe_menu_actions(
                trigger,
                revealed_catalog,
                parent_setup_actions,
            )
            for menu_item in revealed_catalog:
                if str(menu_item.get("role") or "").lower() == "menuitem":
                    selector = str(menu_item.get("selector") or "")
                    if selector and not any(
                        str(existing.get("selector") or "") == selector
                        for existing in expanded_catalog
                    ):
                        expanded_catalog.append(menu_item)
                    if "click" in set(menu_item.get("allowed_actions") or []):
                        menu_item_name = str(menu_item.get("name") or "").strip()
                        if menu_item_name:
                            dynamic_discovered.append({
                                "selector": selector,
                                "label": redact_text(menu_item_name),
                                "trigger": redact_text(trigger_name),
                            })
            if menu_names:
                menu_values = [
                    _factor_value(
                        label=menu_name,
                        action=ActionType.SELECT_OPTION,
                        target=trigger_selector,
                        value=menu_name,
                        item=trigger,
                    )
                    for menu_name in menu_names
                ]
                if not factor_domain_exists("custom_dropdown", trigger_name or "Menu", menu_values):
                    factors.append({
                        "id": f"factor-{len(factors) + 1}",
                        "label": redact_text(trigger_name or "Menu"),
                        "kind": "custom_dropdown",
                        "values": menu_values,
                        "setup_actions": [dict(action) for action in parent_setup_actions],
                    })
                expanded_catalog.extend(revealed_menu_options)
                dynamic_discovered.append({
                    "selector": trigger_selector,
                    "label": redact_text(f"{trigger_name or 'Menu'} options"),
                    "trigger": redact_text(">".join(
                        str(action.get("label") or "") for action in setup_actions
                    )),
                })
                added_for_trigger = True
            elif menu_actions_added:
                menu_open_values = [_factor_value(
                    label="open dropdown",
                    action=ActionType.CLICK,
                    target=trigger_selector,
                    value=None,
                    item=trigger,
                )]
                if not factor_domain_exists(
                    "custom_dropdown_without_exposed_options",
                    trigger_name or "Menu",
                    menu_open_values,
                ):
                    factors.append({
                        "id": f"factor-{len(factors) + 1}",
                        "label": redact_text(trigger_name or "Menu"),
                        "kind": "custom_dropdown_without_exposed_options",
                        "values": menu_open_values,
                        "setup_actions": [dict(action) for action in parent_setup_actions],
                    })
                added_for_trigger = True
            elif len(linked_routes) > links_before_trigger:
                route_open_values = [_factor_value(
                    label="open linked menu",
                    action=ActionType.CLICK,
                    target=trigger_selector,
                    value=None,
                    item=trigger,
                )]
                if not factor_domain_exists(
                    "navigation_menu",
                    trigger_name or "Navigation menu",
                    route_open_values,
                ):
                    factors.append({
                        "id": f"factor-{len(factors) + 1}",
                        "label": redact_text(trigger_name or "Navigation menu"),
                        "kind": "navigation_menu",
                        "values": route_open_values,
                        "setup_actions": [dict(action) for action in parent_setup_actions],
                    })
                dynamic_discovered.append({
                    "selector": trigger_selector,
                    "label": redact_text(trigger_name or "Navigation menu"),
                    "trigger": redact_text(trigger_name),
                })
                added_for_trigger = True
            for revealed in revealed_catalog:
                revealed_selector = str(revealed.get("selector") or "")
                if not revealed_selector or revealed_selector in initial_selectors:
                    continue
                if revealed in revealed_menu_options or str(revealed.get("role") or "").lower() == "menuitem":
                    continue
                if any(str(entry.get("selector") or "") == revealed_selector for entry in expanded_catalog):
                    continue
                revealed_role = str(revealed.get("role") or "").lower()
                revealed_tag = str(revealed.get("tag") or "").lower()
                revealed_name = str(revealed.get("name") or revealed.get("placeholder") or "").strip()
                revealed_actions = set(revealed.get("allowed_actions") or [])
                revealed_setup_actions = [dict(action) for action in setup_actions]

                if str(revealed.get("href") or ""):
                    record_linked_route(str(revealed["href"]))
                    expanded_catalog.append(revealed)
                    continue

                if str(revealed.get("role") or "").lower() == "button" and "click" in revealed_actions:
                    # Test the revealed button, then inspect it as another safe
                    # disclosure trigger while retaining the full opening path.
                    factors.append({
                        "id": f"factor-{len(factors) + 1}",
                        "label": redact_text(revealed_name or "Button action"),
                        "kind": "safe_button",
                        "values": [
                            _factor_value(label="not clicked", action=None, target=revealed_selector, value=None, item=revealed),
                            _factor_value(label="clicked", action=ActionType.CLICK, target=revealed_selector, value=None, item=revealed),
                        ],
                        "setup_actions": revealed_setup_actions,
                    })
                    expanded_catalog.append(revealed)
                    dynamic_discovered.append({"selector": revealed_selector, "label": redact_text(revealed_name), "trigger": redact_text(trigger_name)})
                    if _is_safe_discovery_trigger(revealed):
                        trigger_queue.append((revealed, revealed_setup_actions))
                    added_for_trigger = True
                    continue

                if revealed_role in {"textbox", "searchbox"} and "type_text" in revealed_actions:
                    dynamic_values = _unique_strings(["", *text_values])
                    factor_values = [
                        _factor_value(
                            label="(empty)" if value == "" else value,
                            action=ActionType.TYPE_TEXT,
                            target=revealed_selector,
                            value=value,
                            item=revealed,
                        )
                        for value in dynamic_values
                    ]
                    if factor_values:
                        factors.append({
                            "id": f"factor-{len(factors) + 1}",
                            "label": redact_text(revealed_name or "Search input"),
                            "kind": "search_input" if revealed_role == "searchbox" or any(
                                word in revealed_name.lower() for word in ("search", "filter")
                            ) else "text_input",
                            "values": factor_values,
                            "setup_actions": revealed_setup_actions,
                        })
                        dynamic_discovered.append({"selector": revealed_selector, "label": redact_text(revealed_name), "trigger": redact_text(trigger_name)})
                        expanded_catalog.append(revealed)
                        added_for_trigger = True
                    continue

                if revealed_role == "combobox" and revealed_tag == "select" and "select_option" in revealed_actions:
                    factor_values = [
                        _factor_value(
                            label=str(option.get("label") or option.get("value") or "(empty)"),
                            action=ActionType.SELECT_OPTION,
                            target=revealed_selector,
                            value=str(option.get("value") or ""),
                            item=revealed,
                            select_by_value=True,
                        )
                        for option in revealed.get("options") or []
                        if not option.get("disabled")
                    ]
                    if factor_values:
                        factors.append({
                            "id": f"factor-{len(factors) + 1}",
                            "label": redact_text(revealed_name or "Dropdown"),
                            "kind": "native_select",
                            "values": factor_values,
                            "setup_actions": revealed_setup_actions,
                        })
                        dynamic_discovered.append({"selector": revealed_selector, "label": redact_text(revealed_name), "trigger": redact_text(trigger_name)})
                        expanded_catalog.append(revealed)
                        added_for_trigger = True
                    continue

                if revealed_role == "combobox" and revealed_tag != "select" and "click" in revealed_actions:
                    links_before_dropdown = len(linked_routes)
                    menu_actions_added = 0
                    try:
                        await _click_discovered_control(page, revealed, timeout_ms=2000)
                        options = page.locator('[role="option"]:visible')
                        try:
                            await options.first.wait_for(state="visible", timeout=1200)
                        except Exception:
                            pass
                        opened_dropdown_catalog = await FunctionalSpider.extract_interactive_catalog(page)
                        for opened_item in opened_dropdown_catalog:
                            if opened_item.get("href"):
                                record_linked_route(str(opened_item["href"]))
                        menu_actions_added = add_safe_menu_actions(
                            revealed,
                            opened_dropdown_catalog,
                            revealed_setup_actions,
                        )
                        option_names = []
                        for option_index in range(min(await options.count(), 200)):
                            option = options.nth(option_index)
                            option_metadata = await option.evaluate(
                                "element => ({ tag: element.tagName.toLowerCase(), href: element.getAttribute('href') })"
                            )
                            if option_metadata.get("tag") == "a" or option_metadata.get("href"):
                                record_linked_route(option_metadata.get("href"))
                                continue
                            if (await option.get_attribute("aria-disabled") or "").lower() == "true":
                                continue
                            option_name = (await option.get_attribute("aria-label")) or (await option.inner_text())
                            option_name = " ".join(str(option_name).split())
                            if option_name:
                                option_names.append(option_name)
                        retain_open_menu_controls(opened_dropdown_catalog)
                        option_names = _unique_strings(option_names)
                        if option_names:
                            factors.append({
                                "id": f"factor-{len(factors) + 1}",
                                "label": redact_text(revealed_name or "Dropdown"),
                                "kind": "custom_dropdown",
                                "values": [
                                    _factor_value(
                                        label=option_name,
                                        action=ActionType.SELECT_OPTION,
                                        target=revealed_selector,
                                        value=option_name,
                                        item=revealed,
                                    )
                                    for option_name in option_names
                                ],
                                "setup_actions": revealed_setup_actions,
                            })
                            dynamic_discovered.append({"selector": revealed_selector, "label": redact_text(revealed_name), "trigger": redact_text(trigger_name)})
                            expanded_catalog.append(revealed)
                            added_for_trigger = True
                        elif len(linked_routes) > links_before_dropdown:
                            factors.append({
                                "id": f"factor-{len(factors) + 1}",
                                "label": redact_text(revealed_name or "Navigation menu"),
                                "kind": "navigation_menu",
                                "values": [_factor_value(
                                    label="open linked menu",
                                    action=ActionType.CLICK,
                                    target=revealed_selector,
                                    value=None,
                                    item=revealed,
                                )],
                                "setup_actions": revealed_setup_actions,
                            })
                            dynamic_discovered.append({
                                "selector": revealed_selector,
                                "label": redact_text(revealed_name or "Navigation menu"),
                                "trigger": redact_text(trigger_name),
                            })
                            expanded_catalog.append(revealed)
                            added_for_trigger = True
                        elif menu_actions_added:
                            factors.append({
                                "id": f"factor-{len(factors) + 1}",
                                "label": redact_text(revealed_name or "Dropdown"),
                                "kind": "custom_dropdown_without_exposed_options",
                                "values": [_factor_value(
                                    label="open dropdown",
                                    action=ActionType.CLICK,
                                    target=revealed_selector,
                                    value=None,
                                    item=revealed,
                                )],
                                "setup_actions": revealed_setup_actions,
                            })
                            dynamic_discovered.append({
                                "selector": revealed_selector,
                                "label": redact_text(revealed_name or "Dropdown"),
                                "trigger": redact_text(trigger_name),
                            })
                            expanded_catalog.append(revealed)
                            added_for_trigger = True
                        elif revealed.get("has_popup"):
                            undiscovered_custom_dropdowns += 1
                            unsupported_controls.append({
                                "label": redact_text(revealed_name or "Dropdown"),
                                "reason": "it appeared after opening a control but exposed no accessible option values",
                            })
                    except Exception as error:
                        discovery_errors.append(
                            f"Could not inspect revealed dropdown {redact_text(revealed_name)}: {redact_text(str(error))[:160]}"
                        )
                    finally:
                        try:
                            await page.keyboard.press("Escape")
                        except Exception:
                            pass

            if added_for_trigger:
                # The click is represented as a prerequisite of the revealed
                # controls, avoiding a duplicate independent toggle factor.
                prerequisite_targets = {str(action.get("target") or "") for action in setup_actions}
                factors = [
                    factor for factor in factors
                    if not (
                        factor.get("kind") == "safe_button"
                        and any(value.get("target") in prerequisite_targets for value in factor.get("values") or [])
                    )
                ]
        except Exception as error:
            discovery_errors.append(
                f"Could not inspect controls revealed by {redact_text(trigger_name or trigger_selector)}: {redact_text(str(error))[:160]}"
            )
        finally:
            try:
                await page.keyboard.press("Escape")
                await page.keyboard.press("Escape")
            except Exception:
                pass
            if added_for_trigger and page.url == original_url:
                try:
                    await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                    await wait_for_page_ready(page, timeout_ms=5000)
                except Exception as error:
                    discovery_complete = False
                    discovery_errors.append(f"Could not reset the selected page after staged control discovery: {redact_text(str(error))[:160]}")
            if page.url != original_url:
                try:
                    await page.goto(original_url, wait_until="domcontentloaded", timeout=15000)
                    await wait_for_page_ready(page, timeout_ms=5000)
                except Exception as error:
                    discovery_complete = False
                    discovery_errors.append(f"Could not restore the selected page after dynamic discovery: {redact_text(str(error))[:160]}")

    if page.url != original_url:
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            await wait_for_page_ready(page, timeout_ms=5000)
        except Exception as error:
            discovery_complete = False
            discovery_errors.append(f"Could not restore the selected page after control discovery: {redact_text(str(error))[:180]}")

    grouped_factors = _group_equivalent_safe_buttons(factors)
    selected_categories = set(included_categories or [
        "dropdown", "search", "navigation", "button", "form"
    ])
    selected_factors = [
        factor for factor in grouped_factors
        if factor_category(factor) in selected_categories
    ]
    factors = _prioritize_factors(selected_factors, priority_order)

    return {
        "factors": factors,
        "control_snapshot": {
            str(item.get("selector")): {
                "role": item.get("role"),
                "tag": item.get("tag"),
                "name": redact_text(str(item.get("name") or "")),
                "options": sorted(
                    (str(option.get("value") or ""), str(option.get("label") or ""))
                    for option in item.get("options") or []
                ),
            }
            for item in expanded_catalog if item.get("selector")
        },
        "interactive_controls_discovered": len(expanded_catalog),
        "factors_discovered": len(factors),
        "factors_discovered_before_category_filter": len(grouped_factors),
        "interaction_categories": sorted(selected_categories),
        "priority_order": list(priority_order or DEFAULT_INTERACTION_PRIORITY_ORDER),
        "dynamic_controls_discovered": dynamic_discovered,
        "linked_routes": linked_routes,
        "undiscovered_custom_dropdowns": undiscovered_custom_dropdowns,
        "discovery_errors": discovery_errors,
        "discovery_complete": discovery_complete,
        "unsupported_controls": unsupported_controls,
    }


def estimate_case_count(
    factors: List[Dict[str, Any]],
    include_action_order_permutations: bool,
    priority_order: Optional[List[str]] = None,
) -> int:
    if not factors:
        return 0
    if not include_action_order_permutations:
        return math.prod(len(factor.get("values") or []) for factor in factors)

    # Keep priority tiers fixed in action order. Permute only actions within
    # the same tier, and ignore no-op values when counting permutations.
    categories = _ordered_priority_categories(priority_order)
    category_indexes = {category: index for index, category in enumerate(categories)}
    active_counts = {tuple(0 for _ in categories): 1}
    for factor in factors:
        values = factor.get("values") or []
        inactive = sum(1 for value in values if value.get("action") is None)
        active = sum(1 for value in values if value.get("action") is not None)
        category_index = category_indexes[_factor_priority_kind(factor)]
        next_counts: Dict[tuple, int] = {}
        for prior_counts, combinations in active_counts.items():
            if inactive:
                next_counts[prior_counts] = next_counts.get(prior_counts, 0) + combinations * inactive
            if active:
                next_active = list(prior_counts)
                next_active[category_index] += 1
                next_active_tuple = tuple(next_active)
                next_counts[next_active_tuple] = next_counts.get(next_active_tuple, 0) + combinations * active
        active_counts = next_counts
    return sum(
        combinations * math.prod(math.factorial(count) for count in counts)
        for counts, combinations in active_counts.items()
    )


def _normalize_label(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def resolve_required_combinations(
    factors: List[Dict[str, Any]],
    required_combinations: Optional[List[Dict[str, str]]] = None,
) -> tuple[List[tuple[int, ...]], List[str]]:
    """Resolve factor labels/IDs and value labels into stable domain indexes."""
    default_indexes = tuple(
        next(
            (index for index, value in enumerate(factor.get("values") or []) if value.get("action") is None),
            0,
        )
        for factor in factors
    )
    rows: List[tuple[int, ...]] = []
    errors: List[str] = []
    for combo_number, combo in enumerate(required_combinations or [], start=1):
        row = list(default_indexes)
        for control_label, value_label in combo.items():
            factor_matches = [
                index for index, factor in enumerate(factors)
                if _normalize_label(factor.get("id")) == _normalize_label(control_label)
            ]
            if not factor_matches:
                factor_matches = [
                    index for index, factor in enumerate(factors)
                    if _normalize_label(factor.get("label")) == _normalize_label(control_label)
                ]
            if len(factor_matches) != 1:
                reason = "was not found" if not factor_matches else "matches more than one discovered control"
                errors.append(f"Required combination {combo_number}: control '{control_label}' {reason}.")
                continue
            factor_index = factor_matches[0]
            value_matches = [
                index for index, value in enumerate(factors[factor_index].get("values") or [])
                if _normalize_label(value.get("label")) == _normalize_label(value_label)
            ]
            if len(value_matches) != 1:
                reason = "was not found" if not value_matches else "matches more than one value"
                errors.append(
                    f"Required combination {combo_number}: value '{value_label}' {reason} "
                    f"for control '{control_label}'."
                )
                continue
            row[factor_index] = value_matches[0]
        row_tuple = tuple(row)
        if row_tuple not in rows:
            rows.append(row_tuple)
    return rows, errors


def pairwise_case_indexes(
    factors: List[Dict[str, Any]],
    required_rows: Optional[List[tuple[int, ...]]] = None,
) -> tuple[List[tuple[int, ...]], Optional[str]]:
    """Build a deterministic greedy covering array for every pair of factors."""
    if not factors:
        return [], None
    domains = [factor.get("values") or [] for factor in factors]
    if any(not domain for domain in domains):
        return [], "Pairwise coverage cannot run because a discovered control has no values."

    requirement_count = sum(len(domain) for domain in domains)
    requirement_count += sum(
        len(domains[left]) * len(domains[right])
        for left in range(len(domains))
        for right in range(left + 1, len(domains))
    )
    if requirement_count > MAX_PAIRWISE_REQUIREMENTS:
        return [], (
            f"Pairwise plan has {requirement_count} value-pair requirements, above the safe limit "
            f"of {MAX_PAIRWISE_REQUIREMENTS}; select fewer control categories or values."
        )

    default_row = tuple(
        next(
            (index for index, value in enumerate(domain) if value.get("action") is None),
            0,
        )
        for domain in domains
    )
    rows: List[tuple[int, ...]] = []
    for row in [default_row, *(required_rows or [])]:
        if len(row) == len(domains) and row not in rows:
            rows.append(row)

    uncovered = {
        (factor_index, value_index, -1, -1)
        for factor_index, domain in enumerate(domains)
        for value_index in range(len(domain))
    }
    uncovered.update(
        (left, left_value, right, right_value)
        for left in range(len(domains))
        for right in range(left + 1, len(domains))
        for left_value in range(len(domains[left]))
        for right_value in range(len(domains[right]))
    )

    def remove_covered(row: tuple[int, ...]) -> None:
        for factor_index, value_index in enumerate(row):
            uncovered.discard((factor_index, value_index, -1, -1))
        for left in range(len(row)):
            for right in range(left + 1, len(row)):
                uncovered.discard((left, row[left], right, row[right]))

    for row in rows:
        remove_covered(row)

    while uncovered:
        anchor = min(uncovered, key=lambda item: (item[0], item[2], item[1], item[3]))
        row: List[Optional[int]] = [None] * len(domains)
        row[anchor[0]] = anchor[1]
        if anchor[2] >= 0:
            row[anchor[2]] = anchor[3]

        for factor_index, domain in enumerate(domains):
            if row[factor_index] is not None:
                continue

            def candidate_score(candidate: int) -> int:
                score = int((factor_index, candidate, -1, -1) in uncovered)
                for assigned_index, assigned_value in enumerate(row):
                    if assigned_value is None:
                        continue
                    if assigned_index < factor_index:
                        key = (assigned_index, assigned_value, factor_index, candidate)
                    else:
                        key = (factor_index, candidate, assigned_index, assigned_value)
                    score += int(key in uncovered)
                return score

            row[factor_index] = max(range(len(domain)), key=lambda candidate: (candidate_score(candidate), -candidate))

        completed_row = tuple(int(value) for value in row)
        if completed_row not in rows:
            rows.append(completed_row)
        remove_covered(completed_row)

    return rows, None


def _iter_case_orders(
    factors: List[Dict[str, Any]],
    selected_values: tuple[Dict[str, Any], ...],
    include_action_order_permutations: bool,
    priority_order: Optional[List[str]],
):
    active_indexes = tuple(
        index for index, value in enumerate(selected_values)
        if value.get("action") is not None
    )
    if not include_action_order_permutations:
        yield active_indexes
        return

    categories = _ordered_priority_categories(priority_order)
    active_by_priority = [
        tuple(
            index for index in active_indexes
            if _factor_priority_kind(factors[index]) == category
        )
        for category in categories
    ]

    def combine_priority_orders(category_index: int, prefix: tuple = ()):
        if category_index >= len(active_by_priority):
            yield prefix
            return
        category_indexes = active_by_priority[category_index]
        category_orders = itertools.permutations(category_indexes) if category_indexes else [()]
        for category_order in category_orders:
            yield from combine_priority_orders(category_index + 1, (*prefix, *category_order))

    yield from combine_priority_orders(0)


def _count_case_orders(
    factors: List[Dict[str, Any]],
    selected_values: tuple[Dict[str, Any], ...],
    include_action_order_permutations: bool,
    priority_order: Optional[List[str]],
) -> int:
    if not include_action_order_permutations:
        return 1
    categories = _ordered_priority_categories(priority_order)
    return math.prod(
        math.factorial(sum(
            value.get("action") is not None
            and _factor_priority_kind(factors[index]) == category
            for index, value in enumerate(selected_values)
        ))
        for category in categories
    )


def build_interaction_plan(
    factors: List[Dict[str, Any]],
    *,
    coverage_mode: str,
    include_action_order_permutations: bool,
    priority_order: Optional[List[str]],
    required_combinations: Optional[List[Dict[str, str]]],
) -> tuple[int, List[tuple[int, ...]], List[str]]:
    """Return planned case count, pairwise rows/required rows, and config errors."""
    required_rows, errors = resolve_required_combinations(factors, required_combinations)
    if coverage_mode == "pairwise":
        rows, plan_error = pairwise_case_indexes(factors, required_rows)
        if plan_error:
            errors.append(plan_error)
            return 0, rows, errors
        planned = 0
        for row in rows:
            selected = tuple(factors[index]["values"][value_index] for index, value_index in enumerate(row))
            planned += _count_case_orders(
                factors, selected, include_action_order_permutations, priority_order
            )
        return planned, rows, errors

    return estimate_case_count(factors, include_action_order_permutations, priority_order), required_rows, errors


def interaction_manifest_fingerprint(
    target_url: str,
    factors: List[Dict[str, Any]],
    include_action_order_permutations: bool,
    priority_order: Optional[List[str]] = None,
    coverage_mode: str = "exhaustive",
    planned_rows: Optional[List[tuple[int, ...]]] = None,
) -> str:
    """Stable identity for deciding whether a saved page cursor is still valid."""
    manifest = {
        "target_url": target_url,
        "include_action_order_permutations": include_action_order_permutations,
        "priority_order": list(priority_order or DEFAULT_INTERACTION_PRIORITY_ORDER),
        "coverage_mode": coverage_mode,
        "planned_rows": planned_rows,
        "case_order_version": INTERACTION_CASE_ORDER_VERSION,
        "factors": [
            {
                "id": factor.get("id"),
                "kind": factor.get("kind"),
                "label": factor.get("label"),
                "values": [
                    {
                        "action": value.get("action"),
                        "target": value.get("target"),
                        "value": value.get("value"),
                        "label": value.get("label"),
                        "select_by_value": bool(value.get("select_by_value")),
                    }
                    for value in factor.get("values") or []
                ],
                "setup_actions": factor.get("setup_actions") or [],
            }
            for factor in factors
        ],
    }
    serialized = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


async def execute_interaction_matrix(
    page: Page,
    *,
    target_url: str,
    run_id: str,
    factors: List[Dict[str, Any]],
    max_generated_cases: int,
    include_action_order_permutations: bool,
    priority_order: Optional[List[str]] = None,
    coverage_mode: str = "exhaustive",
    required_combinations: Optional[List[Dict[str, str]]] = None,
    baseline_controls: Optional[Dict[str, Dict[str, Any]]] = None,
    resume_state: Optional[Dict[str, Any]] = None,
    checkpoint_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    deadline: Optional[float] = None,
    cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """Execute a stable manifest prefix, resetting the selected URL per case."""
    planned, planned_rows, plan_errors = build_interaction_plan(
        factors,
        coverage_mode=coverage_mode,
        include_action_order_permutations=include_action_order_permutations,
        priority_order=priority_order,
        required_combinations=required_combinations,
    )
    manifest_fingerprint = interaction_manifest_fingerprint(
        target_url,
        factors,
        include_action_order_permutations,
        priority_order,
        coverage_mode,
        planned_rows,
    )
    resume_state = resume_state or {}
    saved_cursor = int(resume_state.get("next_case_index") or 0)
    saved_executed = int(resume_state.get("executed_cases") or 0)
    resume_matches = (
        resume_state.get("manifest_fingerprint") == manifest_fingerprint
        and saved_cursor == saved_executed
        and 0 <= saved_cursor <= planned
    )
    resume_manifest_changed = bool(resume_state.get("manifest_fingerprint")) and not resume_matches
    start_case_index = int(resume_state.get("next_case_index") or 0) if resume_matches else 0
    start_case_index = min(max(0, start_case_index), planned)
    result: Dict[str, Any] = {
        "mode": "pairwise" if coverage_mode == "pairwise" else "exhaustive_cartesian",
        "coverage_mode": coverage_mode,
        "page_url": redact_url(target_url),
        "factors": [
            {
                "id": factor["id"],
                "label": factor["label"],
                "kind": factor["kind"],
                "values": [value["label"] for value in factor["values"]],
                "setup_actions": [action.get("label") for action in factor.get("setup_actions") or []],
            }
            for factor in factors
        ],
        "factors_discovered": len(factors),
        "planned_cases": planned,
        "executed_cases": int(resume_state.get("executed_cases") or 0) if resume_matches else 0,
        "passed_cases": int(resume_state.get("passed_cases") or 0) if resume_matches else 0,
        "failed_cases": int(resume_state.get("failed_cases") or 0) if resume_matches else 0,
        "blocked_cases": int(resume_state.get("blocked_cases") or 0) if resume_matches else 0,
        "pending_cases": planned - (int(resume_state.get("executed_cases") or 0) if resume_matches else 0),
        "case_limit": max_generated_cases,
        "limit_exceeded": False,
        "action_order_permutations": include_action_order_permutations,
        "priority_order": list(priority_order or DEFAULT_INTERACTION_PRIORITY_ORDER),
        "complete": not plan_errors and (planned == 0 or (resume_matches and start_case_index == planned)),
        "interruption_reason": None,
        "test_cases": list(resume_state.get("test_cases") or []) if resume_matches else [],
        "dynamic_controls": [],
        "linked_routes": [],
        "manifest_fingerprint": manifest_fingerprint,
        "next_case_index": start_case_index,
        "resume_manifest_changed": resume_manifest_changed,
    }
    if plan_errors:
        result["complete"] = False
        result["required_combination_errors"] = plan_errors
        result["interruption_reason"] = "; ".join(plan_errors)
        return result

    baseline_controls = baseline_controls or {}
    dynamic_control_keys = set()
    known_menu_option_names = {
        str(value.get("label") or "")
        for factor in factors
        if factor.get("kind") in {"custom_dropdown", "custom_dropdown_without_exposed_options", "navigation_menu"}
        for value in factor.get("values") or []
    }
    if not factors:
        result["complete"] = True
        result["interruption_reason"] = "No eligible controls remained after applying selected interaction categories."
        return result
    persisted_manifest = [
        {
            "id": factor["id"],
            "label": factor["label"],
            "kind": factor["kind"],
            "values": [dict(value) for value in factor["values"]],
            "setup_actions": [dict(action) for action in factor.get("setup_actions") or []],
        }
        for factor in factors
    ]

    if checkpoint_callback and not resume_matches:
        try:
            await checkpoint_callback({
                "initialize": True,
                "manifest": persisted_manifest,
                "manifest_fingerprint": manifest_fingerprint,
                "next_case_index": 0,
                "executed_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "blocked_cases": 0,
            })
        except Exception as error:
            result["complete"] = False
            result["interruption_reason"] = f"Could not initialize the interaction resume manifest: {redact_text(str(error))[:180]}"
            return result

    def iter_manifest_cases():
        if coverage_mode == "pairwise":
            assignments = (
                tuple(factors[index]["values"][value_index] for index, value_index in enumerate(row))
                for row in planned_rows
            )
        else:
            factor_positions = {factor["id"]: index for index, factor in enumerate(factors)}
            priority_categories = _ordered_priority_categories(priority_order)
            priority_rank = {category: index for index, category in enumerate(priority_categories)}
            # Secondary dimensions stay at their discovered starting state while
            # the P1 dimensions cycle first. Then the full Cartesian space continues.
            case_order_factors = sorted(
                factors,
                key=lambda factor: (
                    0 if _factor_priority_kind(factor) == "other" else 1,
                    factor_positions[factor["id"]]
                    if _factor_priority_kind(factor) == "other"
                    else -priority_rank.get(_factor_priority_kind(factor), len(priority_rank)),
                ),
            )

            def exhaustive_assignments():
                required_set = set(planned_rows)
                for row in planned_rows:
                    yield tuple(
                        factors[index]["values"][value_index]
                        for index, value_index in enumerate(row)
                    )

                for ordered_indexes in itertools.product(
                    *(range(len(factor["values"])) for factor in case_order_factors)
                ):
                    selected_by_factor = {
                        factor["id"]: value_index
                        for factor, value_index in zip(case_order_factors, ordered_indexes)
                    }
                    row = tuple(selected_by_factor[factor["id"]] for factor in factors)
                    if row in required_set:
                        continue
                    yield tuple(
                        factor["values"][selected_by_factor[factor["id"]]]
                        for factor in factors
                    )

            assignments = exhaustive_assignments()

        for selected_values in assignments:
            for order in _iter_case_orders(
                factors,
                selected_values,
                include_action_order_permutations,
                priority_order,
            ):
                yield selected_values, tuple(order)

    batch_executed_cases = 0
    for case_index, (selected_values, order) in enumerate(
        itertools.islice(iter_manifest_cases(), start_case_index, None),
        start=start_case_index,
    ):
            selected_by_factor = {factor["id"]: value for factor, value in zip(factors, selected_values)}
            if cancellation_check and await cancellation_check():
                raise asyncio.CancelledError("Scan cancelled during interaction matrix testing")
            if deadline is not None and time.monotonic() >= deadline:
                result["pending_cases"] = planned - result["executed_cases"]
                result["complete"] = result["pending_cases"] == 0 and result["blocked_cases"] == 0 and not result["dynamic_controls"]
                if result["pending_cases"]:
                    result["interruption_reason"] = "The page interaction time budget ended before the full manifest completed."
                elif result["dynamic_controls"]:
                    result["interruption_reason"] = "Control domains changed during the suite and were not part of the initial manifest."
                return result

            case_ordinal = case_index + 1
            order_labels = [factors[index]["label"] for index in order]
            case_identity = {
                "url": target_url,
                "combination": {
                    factor["id"]: {
                        "label": selected_by_factor[factor["id"]]["label"],
                        "target": selected_by_factor[factor["id"]]["target"],
                        "value": selected_by_factor[factor["id"]].get("value"),
                    }
                    for factor in factors
                },
                "setup_actions": [
                    action
                    for factor, selected in zip(factors, selected_values)
                    if selected.get("action") is not None
                    for action in factor.get("setup_actions") or []
                ],
                "order": list(order),
            }
            case_hash = hashlib.sha1(json.dumps(case_identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
            case_id = f"MATRIX-{case_hash}"
            started = time.perf_counter()
            step_records: List[Dict[str, Any]] = []
            case_failed = False
            case_interrupted = False
            blocked_reason = None

            try:
                response = await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                ready = await wait_for_page_ready(page, timeout_ms=6000)
                if response is not None and response.status >= 500:
                    blocked_reason = f"Selected page returned HTTP {response.status} while resetting this case."
                elif not ready:
                    blocked_reason = "The selected page did not reach an interactive ready state before this case."
            except Exception as error:
                blocked_reason = f"Could not reset the selected page: {redact_text(str(error))[:240]}"

            if blocked_reason:
                case_status = "BLOCKED"
                case_failed = True
                step_records.append({
                    "action": "open_page",
                    "target": redact_url(target_url),
                    "status": "BLOCKED",
                    "error": blocked_reason,
                })
            else:
                for factor_index in order:
                    if blocked_reason:
                        break
                    if cancellation_check and await cancellation_check():
                        raise asyncio.CancelledError("Scan cancelled during exhaustive interaction case")
                    if deadline is not None and time.monotonic() >= deadline:
                        case_interrupted = True
                        case_failed = True
                        blocked_reason = "The page interaction time budget ended while executing this case."
                        step_records.append({
                            "action": "continue_case",
                            "status": "BLOCKED",
                            "error": blocked_reason,
                        })
                        break
                    factor = factors[factor_index]
                    selected_value = selected_by_factor[factor["id"]]
                    if selected_value.get("action") is None:
                        step_records.append({
                            "action": "no_op",
                            "target": selected_value["target"],
                            "status": "PASSED",
                            "expected": "not clicked",
                            "actual": "Control left in its initial state",
                        })
                        continue
                    factor_setup_actions = []
                    setup_targets = set()
                    for candidate_setup in factor.get("setup_actions") or []:
                        candidate_target = str(candidate_setup.get("target") or "")
                        if not candidate_target or candidate_target in setup_targets:
                            continue
                        setup_targets.add(candidate_target)
                        factor_setup_actions.append(candidate_setup)
                    for setup_index, setup_action in enumerate(factor_setup_actions):
                        if cancellation_check and await cancellation_check():
                            raise asyncio.CancelledError("Scan cancelled while opening a required interaction control")
                        setup_target = str(setup_action.get("target") or "")
                        expected_setup_target = (
                            str(factor_setup_actions[setup_index + 1].get("target") or "")
                            if setup_index + 1 < len(factor_setup_actions)
                            else str(selected_value.get("target") or "")
                        )
                        if await _is_disclosure_open(page, setup_action, expected_setup_target):
                            step_records.append({
                                "action": "setup_click",
                                "target": setup_target,
                                "status": "PASSED",
                                "expected": f"open {setup_action.get('label') or 'required control'}",
                                "actual": "Required popup was already open",
                            })
                            continue
                        before_setup_surface = await _control_surface_fingerprint(page)
                        setup_step = ActionStep(
                            action=ActionType.CLICK,
                            target=setup_target,
                            fallback_role=setup_action.get("fallback_role"),
                            fallback_name=setup_action.get("fallback_name"),
                            timeout_ms=5000,
                            description=f"Open control required for {setup_action.get('label') or 'interaction'}",
                        )
                        existing_setup_pages = set(page.context.pages)
                        setup_result = await ActionRunner.execute_step(page, setup_step)
                        try:
                            await page.locator(expected_setup_target).first.wait_for(
                                state="visible",
                                timeout=1500,
                            )
                        except Exception:
                            pass
                        await page.wait_for_timeout(100)
                        after_setup_surface = await _control_surface_fingerprint(page)
                        setup_status = setup_result.get("status", "FAILED")
                        opened_setup_pages = [candidate for candidate in page.context.pages if candidate not in existing_setup_pages]
                        for candidate in opened_setup_pages:
                            try:
                                await candidate.close()
                            except Exception:
                                pass
                        current_setup_route = _same_origin_http_route(page.url, target_url)
                        selected_setup_route = _same_origin_http_route(target_url, target_url)
                        if opened_setup_pages or current_setup_route != selected_setup_route:
                            setup_status = "FAILED"
                            setup_result["error"] = "Opening the required control left the selected page route."
                            try:
                                await page.goto(target_url, wait_until="domcontentloaded", timeout=10000)
                                await wait_for_page_ready(page, timeout_ms=5000)
                            except Exception:
                                pass
                        elif setup_status == "PASSED" and (
                            before_setup_surface == after_setup_surface
                            and not await _is_disclosure_open(page, setup_action, expected_setup_target)
                        ):
                            setup_status = "FAILED"
                            setup_result["error"] = "The required dropdown or popup did not become visible after its trigger was clicked."
                        step_records.append({
                            "action": "setup_click",
                            "target": setup_target,
                            "status": setup_status,
                            "expected": f"open {setup_action.get('label') or 'required control'}",
                            "actual": setup_result.get("details") or setup_status,
                            "error": setup_result.get("error"),
                        })
                        if setup_status != "PASSED":
                            case_failed = True
                            blocked_reason = setup_result.get("error") or "Could not open a required interaction control."
                            break
                    if blocked_reason:
                        break
                    action = ActionType(selected_value["action"])
                    before_surface = (
                        await _control_surface_fingerprint(page)
                        if factor.get("kind") in {
                            "safe_button", "custom_dropdown_without_exposed_options", "navigation_menu"
                        } and action == ActionType.CLICK
                        else None
                    )
                    step = ActionStep(
                        action=action,
                        target=selected_value["target"],
                        fallback_role=selected_value.get("fallback_role"),
                        fallback_name=selected_value.get("fallback_name"),
                        value=selected_value.get("value"),
                        select_by_value=bool(selected_value.get("select_by_value", False)),
                        timeout_ms=5000,
                        description=f"{factor['label']} = {selected_value['label']}",
                    )
                    existing_pages = set(page.context.pages)
                    action_result = await ActionRunner.execute_step(page, step)
                    if before_surface is not None:
                        await page.wait_for_timeout(100)
                        after_surface = await _control_surface_fingerprint(page)
                    else:
                        after_surface = None
                    opened_pages = [candidate for candidate in page.context.pages if candidate not in existing_pages]
                    current_route = _same_origin_http_route(page.url, target_url)
                    selected_route = _same_origin_http_route(target_url, target_url)
                    same_selected_path = current_route == selected_route
                    status = action_result.get("status", "FAILED")
                    error = action_result.get("error")
                    navigation_observed = False
                    if opened_pages:
                        popup_urls = []
                        for candidate in opened_pages:
                            try:
                                await candidate.wait_for_load_state("domcontentloaded", timeout=1000)
                            except Exception:
                                pass
                            if candidate.url and candidate.url != "about:blank":
                                route = _same_origin_http_route(candidate.url, target_url)
                                if route and route != selected_route:
                                    if route not in result["linked_routes"]:
                                        result["linked_routes"].append(route)
                                    popup_urls.append(route)
                                elif not route:
                                    popup_urls.append(candidate.url)
                        for candidate in opened_pages:
                            try:
                                await candidate.close()
                            except Exception:
                                pass
                        same_origin_popups = [
                            route for route in popup_urls
                            if _same_origin_http_route(route, target_url)
                        ]
                        external_popups = [
                            route for route in popup_urls
                            if not _same_origin_http_route(route, target_url)
                        ]
                        if same_origin_popups:
                            navigation_observed = True
                        if external_popups:
                            status = "FAILED"
                            error = f"Interaction opened a page outside the selected site: {', '.join(redact_url(route) for route in external_popups[:2])}."
                    if not same_selected_path:
                        if current_route and current_route != selected_route:
                            if current_route not in result["linked_routes"]:
                                result["linked_routes"].append(current_route)
                            navigation_observed = True
                        elif not current_route:
                            status = "FAILED"
                            error = f"Interaction navigated outside the selected site ({redact_url(page.url)})."
                        try:
                            await page.goto(target_url, wait_until="domcontentloaded", timeout=10000)
                            await wait_for_page_ready(page, timeout_ms=5000)
                        except Exception as restore_error:
                            status = "BLOCKED"
                            error = f"Could not return to the selected page after navigation: {redact_text(str(restore_error))[:160]}"
                            case_interrupted = True
                            blocked_reason = error
                    if navigation_observed and status == "PASSED":
                        verified, observed = True, "Same-origin destination recorded for testing after this page's cases finish"
                    else:
                        verified, observed = await _verify_factor_value(
                            page, factor, selected_value, before_surface=before_surface, after_surface=after_surface
                        )
                    if status == "PASSED" and not verified:
                        status = "FAILED"
                        error = f"The control did not reach the expected state; observed {observed!r}."
                    step_records.append({
                        "action": action.value,
                        "target": selected_value["target"],
                        "value": selected_value.get("value"),
                        "status": status,
                        "expected": selected_value["label"],
                        "actual": observed or action_result.get("details") or status,
                        "error": redact_text(str(error))[:300] if error else None,
                    })
                    if status != "PASSED":
                        case_failed = True
                    if not same_selected_path and case_interrupted:
                        break

                if case_interrupted:
                    # The current case is not complete. Keep its index as the
                    # cursor so the next batch can reset the route and retry it.
                    result["next_case_index"] = case_index
                    result["pending_cases"] = planned - result["executed_cases"]
                    result["complete"] = False
                    result["interruption_reason"] = blocked_reason or "The page interaction time budget ended during this case."
                    return result

                case_status = "FAILED" if case_failed else "PASSED"

                try:
                    current_catalog = await FunctionalSpider.extract_interactive_catalog(page)
                    for current_control in current_catalog:
                        current_selector = str(current_control.get("selector") or "")
                        if not current_selector:
                            continue
                        if (
                            str(current_control.get("role") or "").lower() in {"option", "menuitem"}
                            and str(current_control.get("name") or "") in known_menu_option_names
                        ):
                            continue
                        baseline_control = baseline_controls.get(current_selector)
                        if baseline_control is None:
                            dynamic_key = f"new:{current_selector}"
                            if dynamic_key not in dynamic_control_keys:
                                dynamic_control_keys.add(dynamic_key)
                                result["dynamic_controls"].append({
                                    "selector": current_selector,
                                    "role": current_control.get("role"),
                                    "label": redact_text(str(current_control.get("name") or "")),
                                    "reason": "became visible after an interaction and was not part of the initial case manifest",
                                })
                            continue
                        if current_control.get("tag") == "select":
                            options = sorted(
                                (str(option.get("value") or ""), str(option.get("label") or ""))
                                for option in current_control.get("options") or []
                            )
                            if options != baseline_control.get("options", []):
                                dynamic_key = f"options:{current_selector}"
                                if dynamic_key not in dynamic_control_keys:
                                    dynamic_control_keys.add(dynamic_key)
                                    result["dynamic_controls"].append({
                                        "selector": current_selector,
                                        "role": current_control.get("role"),
                                        "label": redact_text(str(current_control.get("name") or "")),
                                        "reason": "its option values changed after another control was changed",
                                    })
                except Exception:
                    pass

            if case_status != "PASSED":
                try:
                    evidence_path = await EvidenceCollector.capture_screenshot(page, run_id, case_id)
                except Exception:
                    evidence_path = None
            else:
                evidence_path = None

            combination = {
                f"{factor['id']} · {factor['label']}": selected_by_factor[factor["id"]]["label"]
                for factor in factors
            }
            case_record = {
                "id": case_id,
                "title": f"Exhaustive combination {case_ordinal}/{planned}",
                "module": "Selected page interactions",
                "test_type": "Combinatorial interaction testing",
                "test_scenario": "Apply each selected interaction value on a clean selected-page load; no-op values leave controls unchanged.",
                "expected_result": "Every discovered control accepts its selected value; safe same-origin destinations are recorded for later page testing.",
                "actual_result": blocked_reason or ("All interactions completed" if case_status == "PASSED" else "One or more interactions failed"),
                "page_url": redact_url(target_url),
                "pillar": "functional",
                "risk": "read_only",
                "coverage_mode": "exhaustive_cartesian",
                "combination": combination,
                "action_order": order_labels,
                "steps": step_records,
                "assertions_count": 0,
                "passed": case_status == "PASSED",
                "status": case_status,
                "duration_sec": round(time.perf_counter() - started, 2),
            }
            if evidence_path:
                case_record["evidence_path"] = evidence_path
            result["test_cases"].append(case_record)
            result["executed_cases"] += 1
            batch_executed_cases += 1
            result["next_case_index"] = case_index + 1
            if case_status == "PASSED":
                result["passed_cases"] += 1
            elif case_status == "BLOCKED":
                result["blocked_cases"] += 1
            else:
                result["failed_cases"] += 1

            if checkpoint_callback:
                try:
                    await checkpoint_callback({
                        "manifest_fingerprint": manifest_fingerprint,
                        "next_case_index": case_index + 1,
                        "executed_cases": result["executed_cases"],
                        "passed_cases": result["passed_cases"],
                        "failed_cases": result["failed_cases"],
                        "blocked_cases": result["blocked_cases"],
                        "case": case_record,
                    })
                except Exception as error:
                    result["pending_cases"] = planned - result["executed_cases"]
                    result["complete"] = False
                    result["interruption_reason"] = f"Could not save the interaction resume checkpoint: {redact_text(str(error))[:180]}"
                    return result

            if case_ordinal == 1 or case_ordinal % 10 == 0 or case_ordinal == planned:
                await _emit_matrix_progress(run_id, case_ordinal, planned, case_status)

            if case_status == "BLOCKED":
                result["interruption_reason"] = blocked_reason or "The selected page could not be reset for the next case."
                result["pending_cases"] = planned - result["executed_cases"]
                return result

            if batch_executed_cases >= max_generated_cases and result["executed_cases"] < planned:
                result["pending_cases"] = planned - result["executed_cases"]
                result["complete"] = False
                result["limit_exceeded"] = True
                result["interruption_reason"] = (
                    ("The control manifest changed; coverage restarted from case 1. " if resume_manifest_changed else "") +
                    f"The configured batch limit of {max_generated_cases} was reached; "
                    f"{result['pending_cases']} cases remain pending. Raise the limit or reduce the selected domains."
                )
                return result

            if deadline is not None and time.monotonic() >= deadline:
                result["pending_cases"] = planned - result["executed_cases"]
                result["complete"] = result["pending_cases"] == 0 and result["blocked_cases"] == 0 and not result["dynamic_controls"]
                if result["pending_cases"]:
                    result["interruption_reason"] = "The page interaction time budget ended before the full manifest completed."
                elif result["dynamic_controls"]:
                    result["interruption_reason"] = "Control domains changed during the suite and were not part of the initial manifest."
                return result

    result["pending_cases"] = planned - result["executed_cases"]
    result["complete"] = result["pending_cases"] == 0 and result["blocked_cases"] == 0
    result["limit_exceeded"] = False
    if result["dynamic_controls"]:
        result["complete"] = False
        result["interruption_reason"] = (
            f"{len(result['dynamic_controls'])} control(s) or option domains changed after interactions and were not in the initial manifest."
        )
    if result["blocked_cases"] and not result["interruption_reason"]:
        result["interruption_reason"] = "One or more cases were blocked while reloading the selected page."
    return result


async def _verify_factor_value(
    page: Page,
    factor: Dict[str, Any],
    value: Dict[str, Any],
    *,
    before_surface: Optional[str] = None,
    after_surface: Optional[str] = None,
) -> tuple[bool, str]:
    """Check that a control visibly accepted the requested finite-domain value."""
    try:
        locator = page.locator(value["target"])
        kind = factor.get("kind")
        if kind == "safe_button":
            if value.get("action") is None:
                return True, "Control left in its initial state"
            changed = bool(before_surface and after_surface and before_surface != after_surface)
            popup_count = await page.locator(
                '[role="dialog"]:visible, [role="menu"]:visible, [role="listbox"]:visible, '
                '[role="option"]:visible, [role="menuitem"]:visible'
            ).count()
            if changed or (value.get("has_popup") and popup_count > 0):
                return True, f"visible interface changed after click (popup items={popup_count})"
            try:
                expanded = (await locator.get_attribute("aria-expanded", timeout=500) or "").lower()
            except Exception:
                # Opening a modal can remove the originating row button from the
                # DOM. A vanished trigger is not a reason to wait out Playwright's
                # default 30-second locator timeout; the visible state snapshot is
                # the authoritative check above.
                expanded = ""
            if expanded == "true":
                return True, "trigger reports aria-expanded=true"
            return False, f"no visible state change after click (aria-expanded={expanded or 'unset'}; popup items={popup_count})"
        if kind == "checkbox":
            actual = await locator.is_checked() if await locator.evaluate("el => el.tagName.toLowerCase()") == "input" else (await locator.get_attribute("aria-checked")) == "true"
            expected = value.get("value") == "true"
            return actual == expected, f"checked={actual}"
        if kind == "radio_group":
            tag_name = await locator.evaluate("el => el.tagName.toLowerCase()")
            actual = await locator.is_checked() if tag_name == "input" else (await locator.get_attribute("aria-checked")) == "true"
            return actual, f"selected={actual}"
        if kind == "tab_group":
            actual = (await locator.get_attribute("aria-selected")) == "true"
            return actual, f"selected={actual}"
        if kind == "native_select":
            selected = await locator.locator("option:checked").inner_text()
            normalized = " ".join(selected.split())
            expected = " ".join(str(value.get("label") or "").split())
            return normalized == expected, f"selected option={normalized!r}"
        if kind in {"search_input", "text_input"}:
            try:
                actual = await locator.input_value()
            except Exception:
                actual = await locator.inner_text()
            expected = str(value.get("value") or "")
            return actual == expected, f"input value={actual!r}"
        if kind == "slider":
            actual = await locator.input_value()
            expected = str(value.get("value") or "")
            return actual == expected, f"range value={actual!r}"
        if kind in {"custom_dropdown_without_exposed_options", "navigation_menu"}:
            expanded = (await locator.get_attribute("aria-expanded")) == "true"
            popup_count = await page.locator(
                '[role="option"]:visible, [role="menuitem"]:visible, [role="listbox"]:visible, [role="menu"]:visible'
            ).count()
            changed = bool(before_surface and after_surface and before_surface != after_surface)
            return expanded or popup_count > 0 or changed, (
                f"expanded={expanded}; visible popup items={popup_count}; visible surface changed={changed}"
            )
        if kind == "custom_dropdown":
            selected_options = page.locator(
                '[role="option"][aria-selected="true"]:visible'
            )
            selected_count = await selected_options.count()
            if selected_count:
                selected_text = " ".join((await selected_options.first.inner_text()).split())
                expected = " ".join(str(value.get("label") or "").split())
                return selected_text == expected, f"selected option={selected_text!r}"
            trigger_text = " ".join((await locator.inner_text()).split())
            expected = " ".join(str(value.get("label") or "").split())
            return expected in trigger_text, f"dropdown text={trigger_text[:180]!r}"
        return True, action_value_summary(value)
    except Exception as error:
        return False, f"Could not verify control state: {redact_text(str(error))[:180]}"


def action_value_summary(value: Dict[str, Any]) -> str:
    action = value.get("action")
    if action == ActionType.SELECT_OPTION.value:
        return f"selected option={value.get('label')!r}"
    if action == ActionType.TYPE_TEXT.value:
        return f"filled value={value.get('value')!r}"
    return f"action={action} completed"


async def _emit_matrix_progress(run_id: str, current: int, planned: int, status: str) -> None:
    try:
        from app.common.progress import ScanProgressTracker
        await ScanProgressTracker.log(
            run_id,
            "FUNCTIONAL",
            f"Exhaustive page matrix: {current}/{planned} cases executed; latest case {status.lower()}.",
            30,
            level="WARNING" if status != "PASSED" else "INFO",
        )
    except Exception:
        pass
