import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio

from test_engine.pillars.functional import combinatorial
from test_engine.schema.actions import ActionType
from workers.tasks import run_scan


def _value(label, action, target, value=None, context_name=""):
    return {
        "label": label,
        "action": action.value if action else None,
        "target": target,
        "value": value,
        "context_name": context_name,
    }


def test_same_name_row_buttons_are_one_alternative_factor_with_context():
    factors = [
        {
            "id": "factor-1",
            "label": "View on map",
            "kind": "safe_button",
            "values": [
                _value("not clicked", None, "#map-1"),
                _value("clicked", ActionType.CLICK, "#map-1", context_name="Road 1"),
            ],
        },
        {
            "id": "factor-2",
            "label": "View on map",
            "kind": "safe_button",
            "values": [
                _value("not clicked", None, "#map-2"),
                _value("clicked", ActionType.CLICK, "#map-2", context_name="Road 2"),
            ],
        },
    ]

    grouped = combinatorial._group_equivalent_safe_buttons(factors)

    assert len(grouped) == 1
    assert grouped[0]["id"] == "factor-1"
    assert grouped[0]["label"] == "View on map (2 similar controls)"
    assert [value["label"] for value in grouped[0]["values"]] == [
        "not clicked", "click Road 1", "click Road 2"
    ]
    assert [value["target"] for value in grouped[0]["values"][1:]] == ["#map-1", "#map-2"]


def test_action_order_count_matches_only_selected_actions():
    factors = [
        {"values": [_value("off", None, "#button"), _value("on", ActionType.CLICK, "#button")]},
        {"values": [_value("empty", ActionType.TYPE_TEXT, "#search", "")]},
        {"values": [_value("all", None, "#filter"), _value("open", ActionType.CLICK, "#filter")]},
    ]

    expected = 0
    import itertools
    import math
    for selected in itertools.product(*(factor["values"] for factor in factors)):
        expected += math.factorial(sum(value["action"] is not None for value in selected))

    assert combinatorial.estimate_case_count(factors, include_action_order_permutations=True) == expected
    assert combinatorial.estimate_case_count(factors, include_action_order_permutations=False) == 4


def test_manifest_fingerprint_changes_when_factors_or_route_change():
    factors = [{
        "id": "factor-1",
        "label": "Search",
        "kind": "search_input",
        "values": [_value("empty", ActionType.TYPE_TEXT, "#search", "")],
    }]
    fingerprint = combinatorial.interaction_manifest_fingerprint("https://example.com/", factors, False)

    assert fingerprint == combinatorial.interaction_manifest_fingerprint("https://example.com/", factors, False)
    assert fingerprint != combinatorial.interaction_manifest_fingerprint("https://example.com/other", factors, False)
    assert fingerprint != combinatorial.interaction_manifest_fingerprint(
        "https://example.com/", factors, True
    )


@pytest.mark.asyncio
async def test_discovery_enumerates_native_custom_dropdowns_and_finite_text_values():
    class FakeOption:
        def __init__(self, label, disabled=False):
            self.label = label
            self.disabled = disabled

        async def get_attribute(self, name):
            if name == "aria-disabled" and self.disabled:
                return "true"
            return None

        async def inner_text(self):
            return self.label

    class FakeOptions:
        first = SimpleNamespace(wait_for=AsyncMock())

        def __init__(self):
            self.items = [FakeOption("Open"), FakeOption("Closed"), FakeOption("Archived", disabled=True)]

        async def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class DiscoveryPage(_FakePage):
        def __init__(self):
            super().__init__()
            self.url = "https://example.com/roads"
            self.keyboard = SimpleNamespace(press=AsyncMock())
            self.option_locator = FakeOptions()
            self.trigger = SimpleNamespace(click=AsyncMock())

        def locator(self, selector):
            return self.option_locator if selector == '[role="option"]:visible' else self.trigger

        async def wait_for_timeout(self, _ms):
            return None

    catalog = [
        {
            "selector": "#region", "role": "combobox", "tag": "select", "type": "select",
            "name": "Region", "allowed_actions": ["select_option"],
            "options": [
                {"value": "north", "label": "North", "disabled": False},
                {"value": "old", "label": "Old", "disabled": True},
            ],
        },
        {
            "selector": "#status", "role": "combobox", "tag": "button", "type": "",
            "name": "Status", "has_popup": "listbox", "allowed_actions": ["click"],
        },
        {
            "selector": "#search", "role": "searchbox", "tag": "input", "type": "search",
            "name": "Search roads", "allowed_actions": ["type_text"],
        },
    ]

    with patch.object(combinatorial.FunctionalSpider, "extract_interactive_catalog", AsyncMock(return_value=catalog)):
        discovery = await combinatorial.discover_interaction_factors(
            DiscoveryPage(),
            target_url="https://example.com/roads",
            text_values=["sample"],
        )

    assert [factor["kind"] for factor in discovery["factors"]] == [
        "native_select", "custom_dropdown", "search_input"
    ]
    assert [value["label"] for value in discovery["factors"][0]["values"]] == ["North"]
    assert [value["label"] for value in discovery["factors"][1]["values"]] == ["Open", "Closed"]
    assert [value["label"] for value in discovery["factors"][2]["values"]] == ["(empty)", "sample"]


class _FakePage:
    def __init__(self):
        self.url = ""
        self.context = SimpleNamespace(pages=[self])

    async def goto(self, url, **_kwargs):
        self.url = url
        return None


@pytest.mark.asyncio
async def test_matrix_resumes_after_batch_without_repeating_saved_cases():
    page = _FakePage()
    factors = [{
        "id": "factor-1",
        "label": "Search",
        "kind": "search_input",
        "values": [
            _value("empty", ActionType.TYPE_TEXT, "#search", ""),
            _value("sample", ActionType.TYPE_TEXT, "#search", "sample"),
            _value("none", ActionType.TYPE_TEXT, "#search", "none"),
        ],
    }]
    checkpoints = []

    async def persist_checkpoint(value):
        checkpoints.append(value)

    with patch.object(combinatorial, "wait_for_page_ready", AsyncMock(return_value=True)), \
         patch.object(combinatorial.FunctionalSpider, "extract_interactive_catalog", AsyncMock(return_value=[])), \
         patch.object(combinatorial.ActionRunner, "execute_step", AsyncMock(return_value={"status": "PASSED"})), \
         patch.object(combinatorial, "_verify_factor_value", AsyncMock(return_value=(True, "accepted"))), \
         patch.object(combinatorial, "_emit_matrix_progress", AsyncMock()):
        first_batch = await combinatorial.execute_interaction_matrix(
            page,
            target_url="https://example.com/search",
            run_id="run-1",
            factors=factors,
            max_generated_cases=2,
            include_action_order_permutations=False,
            checkpoint_callback=persist_checkpoint,
        )

        assert first_batch["executed_cases"] == 2
        assert first_batch["pending_cases"] == 1
        assert first_batch["next_case_index"] == 2

        last_checkpoint = checkpoints[-1]
        resume_state = {
            key: last_checkpoint[key]
            for key in (
                "manifest_fingerprint", "next_case_index", "executed_cases",
                "passed_cases", "failed_cases", "blocked_cases",
            )
        }
        resume_state["test_cases"] = [item["case"] for item in checkpoints if item.get("case")]

        second_batch = await combinatorial.execute_interaction_matrix(
            page,
            target_url="https://example.com/search",
            run_id="run-1",
            factors=factors,
            max_generated_cases=2,
            include_action_order_permutations=False,
            resume_state=resume_state,
            checkpoint_callback=persist_checkpoint,
        )

    ids = [case["id"] for case in second_batch["test_cases"]]
    assert second_batch["complete"] is True
    assert second_batch["executed_cases"] == 3
    assert second_batch["pending_cases"] == 0
    assert len(ids) == len(set(ids)) == 3
    assert len([item for item in checkpoints if item.get("case")]) == 3
    assert second_batch["test_cases"][:2] == first_batch["test_cases"]


@pytest.mark.asyncio
async def test_redis_checkpoint_round_trip_is_idempotent(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.hashes = {}
            self.lists = {}

        async def hgetall(self, key):
            return self.hashes.get(key, {}).copy()

        async def lrange(self, key, start, end):
            return self.lists.get(key, [])[start : None if end == -1 else end + 1]

        async def delete(self, *keys):
            for key in keys:
                self.hashes.pop(key, None)
                self.lists.pop(key, None)

        async def expire(self, *_args):
            return True

        async def aclose(self):
            return None

        async def eval(self, _script, key_count, state_key, cases_key, *args):
            assert key_count == 2
            fingerprint, cursor, executed, passed, failed, blocked, case_json, _ttl, initialize, manifest_json = args
            state = self.hashes.get(state_key, {})
            if state.get("manifest_fingerprint") != fingerprint:
                self.hashes.pop(state_key, None)
                self.lists.pop(cases_key, None)
                state = {}
            if initialize == "1":
                self.hashes[state_key] = {
                    "manifest_fingerprint": fingerprint,
                    "next_case_index": "0",
                    "executed_cases": "0",
                    "passed_cases": "0",
                    "failed_cases": "0",
                    "blocked_cases": "0",
                    "manifest": manifest_json,
                }
                return 1
            current_cursor = int(state.get("next_case_index", 0))
            next_cursor = int(cursor)
            if next_cursor <= current_cursor:
                return 0
            if next_cursor != current_cursor + 1:
                raise ValueError("non-sequential cursor")
            self.lists.setdefault(cases_key, []).append(case_json)
            self.hashes[state_key] = {
                "manifest_fingerprint": fingerprint,
                "next_case_index": str(cursor),
                "executed_cases": str(executed),
                "passed_cases": str(passed),
                "failed_cases": str(failed),
                "blocked_cases": str(blocked),
                "manifest": state.get("manifest", "[]"),
            }
            return 1

    fake_redis = FakeRedis()
    monkeypatch.setattr(redis.asyncio, "from_url", lambda *_args, **_kwargs: fake_redis)
    checkpoint = {
        "manifest_fingerprint": "manifest-1",
        "next_case_index": 1,
        "executed_cases": 1,
        "passed_cases": 1,
        "failed_cases": 0,
        "blocked_cases": 0,
        "case": {"id": "case-1"},
    }

    await run_scan._save_matrix_checkpoint("run-1", "https://example.com/page", {
        "initialize": True,
        "manifest_fingerprint": "manifest-1",
        "next_case_index": 0,
        "executed_cases": 0,
        "passed_cases": 0,
        "failed_cases": 0,
        "blocked_cases": 0,
        "manifest": [{"id": "factor-1", "label": "Search"}],
    })
    await run_scan._save_matrix_checkpoint("run-1", "https://example.com/page", checkpoint)
    await run_scan._save_matrix_checkpoint("run-1", "https://example.com/page", checkpoint)
    loaded = await run_scan._load_matrix_checkpoint("run-1", "https://example.com/page")

    assert loaded["next_case_index"] == 1
    assert loaded["executed_cases"] == 1
    assert loaded["test_cases"] == [{"id": "case-1"}]


def test_only_limit_or_time_interrupted_manifests_are_automatically_resumed():
    base = {
        "complete": False,
        "pending_cases": 10,
        "discovery_complete": True,
        "discovery_errors": [],
        "unsupported_controls": [],
        "dynamic_controls": [],
        "undiscovered_custom_dropdowns": 0,
    }

    assert run_scan._matrix_batch_can_resume([
        {**base, "interruption_reason": "The configured batch limit of 5 was reached"}
    ])
    assert run_scan._matrix_batch_can_resume([
        {**base, "interruption_reason": "The page interaction time budget ended before completion"}
    ])
    assert not run_scan._matrix_batch_can_resume([
        {**base, "interruption_reason": "Dropdown options could not be enumerated", "unsupported_controls": [{"label": "Select", "reason": "inaccessible"}]}
    ])


@pytest.mark.asyncio
async def test_matrix_rejects_and_restores_navigation_outside_selected_route():
    page = _FakePage()
    target_url = "https://example.com/selected"
    factors = [{
        "id": "factor-1",
        "label": "Open details",
        "kind": "safe_button",
        "values": [_value("clicked", ActionType.CLICK, "#details")],
    }]

    async def navigate_away(_page, _step):
        page.url = "https://example.com/other"
        return {"status": "PASSED"}

    with patch.object(combinatorial, "wait_for_page_ready", AsyncMock(return_value=True)), \
         patch.object(combinatorial.FunctionalSpider, "extract_interactive_catalog", AsyncMock(return_value=[])), \
         patch.object(combinatorial.ActionRunner, "execute_step", navigate_away), \
         patch.object(combinatorial, "_verify_factor_value", AsyncMock(return_value=(True, "clicked"))), \
         patch.object(combinatorial, "_emit_matrix_progress", AsyncMock()):
        result = await combinatorial.execute_interaction_matrix(
            page,
            target_url=target_url,
            run_id="run-route",
            factors=factors,
            max_generated_cases=10,
            include_action_order_permutations=False,
        )

    assert result["failed_cases"] == 1
    assert result["test_cases"][0]["steps"][0]["status"] == "FAILED"
    assert "outside the selected page route" in result["test_cases"][0]["steps"][0]["error"]
    assert page.url == target_url


@pytest.mark.asyncio
async def test_matrix_executes_each_selected_action_order():
    page = _FakePage()
    factors = [
        {"id": "factor-1", "label": "First", "kind": "safe_button", "values": [_value("click", ActionType.CLICK, "#first")]},
        {"id": "factor-2", "label": "Second", "kind": "safe_button", "values": [_value("click", ActionType.CLICK, "#second")]},
    ]

    with patch.object(combinatorial, "wait_for_page_ready", AsyncMock(return_value=True)), \
         patch.object(combinatorial.FunctionalSpider, "extract_interactive_catalog", AsyncMock(return_value=[])), \
         patch.object(combinatorial.ActionRunner, "execute_step", AsyncMock(return_value={"status": "PASSED"})), \
         patch.object(combinatorial, "_verify_factor_value", AsyncMock(return_value=(True, "clicked"))), \
         patch.object(combinatorial, "_emit_matrix_progress", AsyncMock()):
        result = await combinatorial.execute_interaction_matrix(
            page,
            target_url="https://example.com/page",
            run_id="run-order",
            factors=factors,
            max_generated_cases=10,
            include_action_order_permutations=True,
        )

    assert result["planned_cases"] == result["executed_cases"] == 2
    assert [case["action_order"] for case in result["test_cases"]] == [
        ["First", "Second"], ["Second", "First"]
    ]


@pytest.mark.asyncio
async def test_matrix_obeys_cancellation_before_starting_the_next_case():
    page = _FakePage()
    factors = [{
        "id": "factor-1",
        "label": "Search",
        "kind": "search_input",
        "values": [_value("empty", ActionType.TYPE_TEXT, "#search", "")],
    }]

    async def cancelled():
        return True

    with pytest.raises(asyncio.CancelledError):
        await combinatorial.execute_interaction_matrix(
            page,
            target_url="https://example.com/search",
            run_id="run-cancel",
            factors=factors,
            max_generated_cases=10,
            include_action_order_permutations=False,
            cancellation_check=cancelled,
        )
