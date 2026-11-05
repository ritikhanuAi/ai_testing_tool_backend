"""Deterministic finite-domain interaction coverage for the current page.

The suite enumerates the Cartesian product of discoverable safe control values. Text
inputs have an explicit finite domain supplied by the scan options. Action-order
permutations are opt-in because they multiply the suite by N!.
"""

import asyncio
import hashlib
import itertools
import json
import math
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from playwright.async_api import Page

from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.evidence import EvidenceCollector
from test_engine.browser.readiness import wait_for_page_ready
from test_engine.crawl.site_crawler import redact_text, redact_url
from test_engine.pillars.functional.spider import FunctionalSpider
from test_engine.schema.actions import ActionStep, ActionType


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
    grouped: Dict[str, Dict[str, Any]] = {}
    result: List[Dict[str, Any]] = []
    for factor in factors:
        if factor.get("kind") != "safe_button":
            result.append(factor)
            continue
        key = " ".join(str(factor.get("label") or "").casefold().split())
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
    }


async def discover_interaction_factors(
    page: Page,
    *,
    target_url: str,
    text_values: List[str],
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
                values.append(_factor_value(
                    label=option_name,
                    action=ActionType.CLICK,
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
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "checkbox",
                "values": [
                    _factor_value(label="unchecked", action=ActionType.SET_CHECKED, target=selector, value="false", item=item),
                    _factor_value(label="checked", action=ActionType.SET_CHECKED, target=selector, value="true", item=item),
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
                    values.append(_factor_value(
                        label=formatted,
                        action=ActionType.SET_RANGE,
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
            try:
                trigger = page.locator(selector)
                await trigger.click(timeout=2500)
                options = page.locator('[role="option"]:visible')
                try:
                    await options.first.wait_for(state="visible", timeout=2500)
                except Exception:
                    pass
                total_option_count = await options.count()
                count = min(total_option_count, 200)
                if total_option_count > 200:
                    unsupported_controls.append({
                        "label": redact_text(name),
                        "reason": f"the dropdown exposes {total_option_count} options; only 200 can be inventoried in one scan",
                    })
                for option_index in range(count):
                    option = options.nth(option_index)
                    disabled = (await option.get_attribute("aria-disabled") or "").lower() == "true"
                    if disabled:
                        continue
                    option_name = (await option.get_attribute("aria-label")) or (await option.inner_text())
                    option_name = " ".join(str(option_name).split())
                    if option_name:
                        option_names.append(option_name)
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
                undiscovered_custom_dropdowns += 1
                values = [_factor_value(
                    label="open dropdown",
                    action=ActionType.CLICK,
                    target=selector,
                    value=None,
                    item=item,
                )]
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "custom_dropdown" if option_names else "custom_dropdown_without_exposed_options",
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
            try:
                await page.locator(selector).click(timeout=2500)
                try:
                    await page.locator('[role="option"]:visible, [role="menuitem"]:visible').first.wait_for(
                        state="visible", timeout=2500
                    )
                except Exception:
                    pass
                for role_selector in ('[role="option"]:visible', '[role="menuitem"]:visible'):
                    menu_items = page.locator(role_selector)
                    total_menu_count = await menu_items.count()
                    if total_menu_count > 200:
                        role_name = "option" if "option" in role_selector else "menuitem"
                        unsupported_controls.append({
                            "label": redact_text(name),
                            "reason": f"the menu exposes {total_menu_count} {role_name} items; only 200 are inventoried",
                        })
                    for menu_index in range(min(total_menu_count, 200)):
                        menu_item = menu_items.nth(menu_index)
                        metadata = await menu_item.evaluate("element => ({ tag: element.tagName.toLowerCase(), href: element.getAttribute('href') })")
                        if metadata.get("tag") == "a" or metadata.get("href"):
                            continue
                        menu_name = (await menu_item.get_attribute("aria-label")) or (await menu_item.inner_text())
                        menu_name = " ".join(str(menu_name).split())
                        if menu_name and not any(word in menu_name.lower() for word in ("delete", "remove", "logout", "log out", "purchase", "checkout", "pay", "submit")):
                            menu_names.append(menu_name)
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
                undiscovered_custom_dropdowns += 1
                values = [_factor_value(
                    label="open dropdown",
                    action=ActionType.CLICK,
                    target=selector,
                    value=None,
                    item=item,
                )]
            factors.append({
                "id": f"factor-{len(factors) + 1}",
                "label": redact_text(name),
                "kind": "custom_dropdown" if menu_names else "custom_dropdown_without_exposed_options",
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
                    "kind": "search_input" if role == "searchbox" or kind == "search" else "text_input",
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

    if page.url != original_url:
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            await wait_for_page_ready(page, timeout_ms=5000)
        except Exception as error:
            discovery_complete = False
            discovery_errors.append(f"Could not restore the selected page after control discovery: {redact_text(str(error))[:180]}")

    factors = _group_equivalent_safe_buttons(factors)

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
            for item in catalog if item.get("selector")
        },
        "interactive_controls_discovered": len(catalog),
        "factors_discovered": len(factors),
        "undiscovered_custom_dropdowns": undiscovered_custom_dropdowns,
        "discovery_errors": discovery_errors,
        "discovery_complete": discovery_complete,
        "unsupported_controls": unsupported_controls,
    }


def estimate_case_count(factors: List[Dict[str, Any]], include_action_order_permutations: bool) -> int:
    if not factors:
        return 0
    if not include_action_order_permutations:
        return math.prod(len(factor.get("values") or []) for factor in factors)

    # A no-op value (for example, leave a button untouched) is not an action
    # whose position needs permuting. Count permutations only across controls
    # that are actually acted on in each value combination.
    active_counts = {0: 1}
    for factor in factors:
        values = factor.get("values") or []
        inactive = sum(1 for value in values if value.get("action") is None)
        active = sum(1 for value in values if value.get("action") is not None)
        next_counts: Dict[int, int] = {}
        for prior_active, combinations in active_counts.items():
            if inactive:
                next_counts[prior_active] = next_counts.get(prior_active, 0) + combinations * inactive
            if active:
                next_active = prior_active + 1
                next_counts[next_active] = next_counts.get(next_active, 0) + combinations * active
        active_counts = next_counts
    return sum(combinations * math.factorial(active_count) for active_count, combinations in active_counts.items())


def interaction_manifest_fingerprint(
    target_url: str,
    factors: List[Dict[str, Any]],
    include_action_order_permutations: bool,
) -> str:
    """Stable identity for deciding whether a saved page cursor is still valid."""
    manifest = {
        "target_url": target_url,
        "include_action_order_permutations": include_action_order_permutations,
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
    baseline_controls: Optional[Dict[str, Dict[str, Any]]] = None,
    resume_state: Optional[Dict[str, Any]] = None,
    checkpoint_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    deadline: Optional[float] = None,
    cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
) -> Dict[str, Any]:
    """Execute a stable manifest prefix, resetting the selected URL per case."""
    planned = estimate_case_count(factors, include_action_order_permutations)
    manifest_fingerprint = interaction_manifest_fingerprint(
        target_url, factors, include_action_order_permutations
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
        "mode": "exhaustive_cartesian",
        "page_url": redact_url(target_url),
        "factors": [
            {"id": factor["id"], "label": factor["label"], "kind": factor["kind"], "values": [value["label"] for value in factor["values"]]}
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
        "complete": planned == 0 or (resume_matches and start_case_index == planned),
        "interruption_reason": None,
        "test_cases": list(resume_state.get("test_cases") or []) if resume_matches else [],
        "dynamic_controls": [],
        "manifest_fingerprint": manifest_fingerprint,
        "next_case_index": start_case_index,
        "resume_manifest_changed": resume_manifest_changed,
    }
    baseline_controls = baseline_controls or {}
    dynamic_control_keys = set()
    known_menu_option_names = {
        str(value.get("label") or "")
        for factor in factors
        if factor.get("kind") in {"custom_dropdown", "custom_dropdown_without_exposed_options"}
        for value in factor.get("values") or []
    }
    if not factors:
        result["interruption_reason"] = "No eligible finite-domain controls were discovered on this page."
        return result
    value_domains = [factor["values"] for factor in factors]
    persisted_manifest = [
        {
            "id": factor["id"],
            "label": factor["label"],
            "kind": factor["kind"],
            "values": [dict(value) for value in factor["values"]],
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
        for selected_values in itertools.product(*value_domains):
            selected_by_factor = {factor["id"]: value for factor, value in zip(factors, selected_values)}
            active_factor_indexes = tuple(
                index for index, factor in enumerate(factors)
                if selected_by_factor.get(factor["id"], {}).get("action") is not None
            )
            orders = itertools.permutations(active_factor_indexes) if include_action_order_permutations else [active_factor_indexes]
            for order in orders:
                yield selected_values, tuple(order)

    batch_executed_cases = 0
    for case_index, (selected_values, order) in enumerate(
        itertools.islice(iter_manifest_cases(), start_case_index, None),
        start=start_case_index,
    ):
            selected_by_factor = {factor["id"]: value for factor, value in zip(factors, selected_values)}
            if cancellation_check and await cancellation_check():
                raise asyncio.CancelledError("Scan cancelled during exhaustive interaction testing")
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
                page_parts = urlsplit(target_url)
                for factor_index in order:
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
                    action = ActionType(selected_value["action"])
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
                    opened_pages = [candidate for candidate in page.context.pages if candidate not in existing_pages]
                    current = urlsplit(page.url)
                    same_selected_path = (
                        current.scheme == page_parts.scheme and current.netloc == page_parts.netloc
                        and current.path.rstrip("/") == page_parts.path.rstrip("/")
                    )
                    status = action_result.get("status", "FAILED")
                    error = action_result.get("error")
                    if opened_pages:
                        popup_urls = [redact_url(candidate.url) for candidate in opened_pages if candidate.url not in {"", "about:blank"}]
                        for candidate in opened_pages:
                            try:
                                await candidate.close()
                            except Exception:
                                pass
                        if popup_urls:
                            status = "FAILED"
                            error = f"Interaction opened another page outside the selected route: {', '.join(popup_urls[:2])}."
                    verified, observed = await _verify_factor_value(page, factor, selected_value)
                    if status == "PASSED" and not verified:
                        status = "FAILED"
                        error = f"The control did not reach the expected state; observed {observed!r}."
                    if not same_selected_path:
                        status = "FAILED"
                        error = f"Interaction navigated outside the selected page route ({redact_url(page.url)})."
                        try:
                            await page.goto(target_url, wait_until="domcontentloaded", timeout=10000)
                            await wait_for_page_ready(page, timeout_ms=5000)
                        except Exception:
                            pass
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
                    if not same_selected_path:
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
                "expected_result": "Every discovered control accepts its selected value without leaving the selected page.",
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


async def _verify_factor_value(page: Page, factor: Dict[str, Any], value: Dict[str, Any]) -> tuple[bool, str]:
    """Check that a control visibly accepted the requested finite-domain value."""
    try:
        locator = page.locator(value["target"])
        kind = factor.get("kind")
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
        if kind == "custom_dropdown_without_exposed_options":
            expanded = (await locator.get_attribute("aria-expanded")) == "true"
            option_count = await page.locator('[role="option"]:visible').count()
            return expanded or option_count > 0, f"expanded={expanded}; visible options={option_count}"
        if kind == "custom_dropdown":
            selected_options = page.locator('[role="option"][aria-selected="true"]:visible')
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
