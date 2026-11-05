"""Human test pack mode must not call the AI anywhere (use_ai=False)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_engine.prompts.accessibility_fixer import AccessibilityFixer
from ai_engine.prompts.test_generation import AITestCaseGenerator
from test_engine.pillars.accessibility import runner as a11y_runner
from test_engine.pillars.accessibility.axe_runner import AxeCoreScanner
from test_engine.pillars.functional import runner as func_runner

VIOLATIONS = {
    "violations": [
        {"id": "image-alt", "impact": "critical", "description": "d", "help": "Images need alt",
         "nodes_count": 1, "selector": "img", "html": "<img>"},
    ],
    "passes_count": 3,
}


def _fake_page() -> MagicMock:
    page = MagicMock()
    page.url = "https://example.com/"
    page.goto = AsyncMock()
    page.add_script_tag = AsyncMock()
    page.evaluate = AsyncMock(return_value=VIOLATIONS)
    page.set_viewport_size = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_axe_without_ai_uses_builtin_fixes_only():
    ai_fix = AsyncMock(return_value="AI fix")
    with patch.object(AccessibilityFixer, "_ai_fix", ai_fix):
        findings, _, _ = await AxeCoreScanner.run_axe(_fake_page(), page_url="u", use_ai=False)
    ai_fix.assert_not_called()
    assert findings and "alt" in findings[0].suggested_fix


@pytest.mark.asyncio
async def test_accessibility_without_ai_skips_vision_and_renormalizes_score():
    vision = AsyncMock(return_value=([], 100.0))
    with patch.object(a11y_runner.AxeCoreScanner, "run_axe",
                      AsyncMock(return_value=([], 100.0, {"axe_audit_completed": True}))), \
         patch.object(a11y_runner.ResponsiveViewportScanner, "check_mobile_overflow",
                      AsyncMock(return_value=([], 100.0))), \
         patch.object(a11y_runner.ReadabilityScanner, "audit_elements",
                      AsyncMock(return_value=([], 100.0))), \
         patch.object(a11y_runner.UXVisionScanner, "audit_visual_design", vision):
        result = await a11y_runner.AccessibilityScanner().scan(
            _fake_page(), "https://example.com/", use_ai=False
        )
    vision.assert_not_called()
    assert result.score == 100.0  # not cut by the 15% vision share
    assert result.status == "PASSED"
    assert result.metrics["vision_review_disabled"] is True
    assert result.metrics["ai_used"] is False


@pytest.mark.asyncio
async def test_accessibility_defaults_to_text_llm_without_sending_screenshots_to_vision():
    vision = AsyncMock(return_value=([], 100.0))
    axe = AsyncMock(return_value=([], 100.0, {"axe_audit_completed": True}))
    with patch.object(a11y_runner.AxeCoreScanner, "run_axe", axe), \
         patch.object(a11y_runner.ResponsiveViewportScanner, "check_mobile_overflow",
                      AsyncMock(return_value=([], 100.0))), \
         patch.object(a11y_runner.ReadabilityScanner, "audit_elements",
                      AsyncMock(return_value=([], 100.0))), \
         patch.object(a11y_runner.UXVisionScanner, "audit_visual_design", vision):
        result = await a11y_runner.AccessibilityScanner().scan(
            _fake_page(), "https://example.com/", use_ai=True
        )

    vision.assert_not_called()
    assert axe.await_args.kwargs["use_ai"] is True
    assert result.metrics["ai_used"] is True
    assert result.metrics["vision_review_disabled"] is True


@pytest.mark.asyncio
async def test_functional_without_ai_skips_form_suite_and_ai_journey_even_for_empty_pack():
    form_suite = AsyncMock(return_value=[])
    journey = AsyncMock()
    page = _fake_page()
    page.on = MagicMock()
    with patch.object(func_runner.FunctionalSpider, "audit_links_and_images", AsyncMock(return_value=[])), \
         patch.object(func_runner.FunctionalSpider, "extract_interactive_inventory",
                      AsyncMock(return_value={"forms": [{"selector": "form"}], "elements": [{"selector": "#a"}]})), \
         patch.object(AITestCaseGenerator, "generate_suite_for_forms", form_suite), \
         patch.object(func_runner.FunctionalScanner, "_run_react_journey", journey), \
         patch.object(func_runner, "_emit_progress", AsyncMock()):
        try:
            await func_runner.FunctionalScanner().scan(page, "https://example.com/", test_cases=[], use_ai=False)
        except Exception:
            pass  # later deterministic checks may need a real page; we only care about the AI gates
    form_suite.assert_not_called()
    journey.assert_not_called()
