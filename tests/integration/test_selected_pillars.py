import pytest
from test_engine.scoring.aggregator import ReportAggregator
from test_engine.pillars.base import PillarScanResult, PillarType

def test_single_selected_pillar_functional_renormalization():
    func_result = PillarScanResult(
        pillar=PillarType.FUNCTIONAL,
        score=80.0,
        status="PASSED",
        findings=[],
        metrics={"finding_count": 0},
    )
    sec_result = PillarScanResult(
        pillar=PillarType.SECURITY,
        score=100.0,
        status="SKIPPED",
        findings=[],
        metrics={"skipped": True, "reason": "disabled by scan profile"},
    )
    perf_result = PillarScanResult(
        pillar=PillarType.PERFORMANCE,
        score=100.0,
        status="SKIPPED",
        findings=[],
        metrics={"skipped": True, "reason": "disabled by scan profile"},
    )
    a11y_result = PillarScanResult(
        pillar=PillarType.ACCESSIBILITY,
        score=100.0,
        status="SKIPPED",
        findings=[],
        metrics={"skipped": True, "reason": "disabled by scan profile"},
    )

    report = ReportAggregator.aggregate(
        run_id="run-test-selected-1",
        target_url="https://example.com",
        pillar_results=[func_result, sec_result, perf_result, a11y_result],
    )

    assert report.pillar_results["functional"].status == "PASSED"
    assert report.pillar_results["security"].status == "SKIPPED"
    assert report.pillar_results["performance"].status == "SKIPPED"
    assert report.pillar_results["accessibility"].status == "SKIPPED"

    # Effective weights must assign 100% (1.0) to functional and 0 to skipped
    assert report.effective_pillar_weights.get("functional") == 1.0
    assert report.effective_pillar_weights.get("security", 0.0) == 0.0
    assert report.effective_pillar_weights.get("performance", 0.0) == 0.0
    assert report.effective_pillar_weights.get("accessibility", 0.0) == 0.0

    # Overall score must reflect functional pillar directly (80.0)
    assert report.overall_score == 80.0


def test_dual_selected_pillars_renormalization():
    func_result = PillarScanResult(
        pillar=PillarType.FUNCTIONAL,
        score=100.0,
        status="SKIPPED",
        findings=[],
        metrics={"skipped": True},
    )
    sec_result = PillarScanResult(
        pillar=PillarType.SECURITY,
        score=90.0,
        status="PASSED",
        findings=[],
        metrics={"finding_count": 0},
    )
    perf_result = PillarScanResult(
        pillar=PillarType.PERFORMANCE,
        score=100.0,
        status="SKIPPED",
        findings=[],
        metrics={"skipped": True},
    )
    a11y_result = PillarScanResult(
        pillar=PillarType.ACCESSIBILITY,
        score=70.0,
        status="WARNING",
        findings=[],
        metrics={"finding_count": 0},
    )

    report = ReportAggregator.aggregate(
        run_id="run-test-selected-2",
        target_url="https://example.com",
        pillar_results=[func_result, sec_result, perf_result, a11y_result],
    )

    # Security base weight 0.25, A11y base weight 0.20 -> sum 0.45
    # Normalized: Security = 0.25 / 0.45 ~= 0.5556, A11y = 0.20 / 0.45 ~= 0.4444
    assert round(report.effective_pillar_weights["security"], 3) == round(0.25 / 0.45, 3)
    assert round(report.effective_pillar_weights["accessibility"], 3) == round(0.20 / 0.45, 3)
    assert report.effective_pillar_weights.get("functional", 0.0) == 0.0
    assert report.effective_pillar_weights.get("performance", 0.0) == 0.0

    expected_overall = round(90.0 * (0.25 / 0.45) + 70.0 * (0.20 / 0.45), 1)
    assert round(report.overall_score, 1) == expected_overall
