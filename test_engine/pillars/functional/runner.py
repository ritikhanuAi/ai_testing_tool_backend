import time
from typing import List, Dict, Any, Optional
from playwright.async_api import Page
from app.common.constants import PillarType, FindingSeverity
from test_engine.pillars.base import BasePillarScanner, PillarScanResult, PillarFinding
from test_engine.schema.case import TestCaseModel
from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.assertion_runner import AssertionRunner
from test_engine.browser.evidence import EvidenceCollector

class FunctionalScanner(BasePillarScanner):
    """Executes human-authored test cases and verifies site functional behavior."""

    @property
    def pillar_type(self) -> PillarType:
        return PillarType.FUNCTIONAL

    async def scan(
        self,
        page: Page,
        target_url: str,
        test_cases: Optional[List[TestCaseModel]] = None,
        run_id: str = "manual_run",
        **kwargs
    ) -> PillarScanResult:
        start_time = time.time()
        findings: List[PillarFinding] = []
        cases = test_cases or []

        total_cases = len(cases)
        passed_cases = 0

        # Listen for uncaught JavaScript errors in browser console
        js_errors: List[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))

        for case in cases:
            case_passed = True
            for step in case.steps:
                step_result = await ActionRunner.execute_step(page, step)
                if step_result["status"] == "FAILED":
                    case_passed = False
                    # Capture screenshot on failure
                    screenshot_path = await EvidenceCollector.capture_screenshot(
                        page, run_id=run_id, case_id=case.id
                    )
                    findings.append(PillarFinding(
                        id=f"FUNC-FAIL-{case.id}",
                        title=f"Step failed in {case.title}",
                        description=f"Action '{step.action.value}' on '{step.target}' failed: {step_result.get('error')}",
                        severity=FindingSeverity.HIGH,
                        evidence_path=screenshot_path,
                        selector_or_url=step.target,
                        suggested_fix="Verify element locator and page state timing."
                    ))
                    break

            if case_passed:
                for assertion in case.assertions:
                    assert_res = await AssertionRunner.evaluate_assertion(page, assertion)
                    if not assert_res.get("passed", False):
                        case_passed = False
                        screenshot_path = await EvidenceCollector.capture_screenshot(
                            page, run_id=run_id, case_id=case.id
                        )
                        findings.append(PillarFinding(
                            id=f"ASSERT-FAIL-{case.id}",
                            title=f"Assertion failed in {case.title}",
                            description=f"Expected: {assert_res.get('expected')}, Got: {assert_res.get('actual')}",
                            severity=FindingSeverity.HIGH,
                            evidence_path=screenshot_path,
                            selector_or_url=assertion.target,
                            suggested_fix="Check if application data or business logic returned the correct response."
                        ))
                        break

            if case_passed:
                passed_cases += 1

        # Check for uncaught JS errors
        if js_errors:
            for i, err in enumerate(js_errors[:5]):
                findings.append(PillarFinding(
                    id=f"JS-ERR-{i+1}",
                    title="Uncaught JavaScript exception in browser",
                    description=err,
                    severity=FindingSeverity.MEDIUM,
                    suggested_fix="Review browser console logs and add try/catch or null checks."
                ))

        # Calculate score: baseline 100%, deduct based on case failures
        score = (passed_cases / total_cases * 100.0) if total_cases > 0 else 100.0
        if js_errors:
            score = max(0.0, score - min(20.0, len(js_errors) * 5.0))

        duration = time.time() - start_time
        status = "PASSED" if score >= 85 else ("WARNING" if score >= 60 else "FAILED")

        return PillarScanResult(
            pillar=self.pillar_type,
            score=round(score, 1),
            status=status,
            findings=findings,
            metrics={
                "total_cases": total_cases,
                "passed_cases": passed_cases,
                "failed_cases": total_cases - passed_cases,
                "js_errors_count": len(js_errors)
            },
            duration_sec=round(duration, 2)
        )
