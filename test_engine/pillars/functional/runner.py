import time
from typing import List, Dict, Any, Optional
from playwright.async_api import Page
from app.common.constants import PillarType, FindingSeverity
from test_engine.pillars.base import BasePillarScanner, PillarScanResult, PillarFinding
from test_engine.schema.case import TestCaseModel
from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.assertion_runner import AssertionRunner
from test_engine.browser.evidence import EvidenceCollector
from test_engine.pillars.functional.spider import FunctionalSpider
from ai_engine.prompts.test_generation import AITestCaseGenerator

class FunctionalScanner(BasePillarScanner):
    """
    Autonomous QA Engineer: Discovers forms & links, synthesizes AI test cases,
    executes user journeys via Playwright, and evaluates functional health.
    """

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

        # 1. Listen for uncaught JavaScript exceptions in the browser console
        js_errors: List[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))

        # 2. Concurrently audit links and broken images
        link_findings = await FunctionalSpider.audit_links_and_images(page, target_url)
        findings.extend(link_findings)

        # 3. Discover interactive forms and inputs in the live DOM
        forms = await FunctionalSpider.extract_forms(page)

        # 4. Generate AI test cases automatically if not manually provided
        cases = list(test_cases) if test_cases else []
        if not cases and forms:
            cases = await AITestCaseGenerator.generate_suite_for_forms(forms, target_url)

        total_cases = len(cases)
        passed_cases = 0

        # 5. Execute all test cases with Playwright
        for case in cases:
            case_passed = True
            for step in case.steps:
                step_result = await ActionRunner.execute_step(page, step)
                if step_result["status"] == "FAILED":
                    case_passed = False
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
                        suggested_fix="Verify element visibility, selector accuracy, or page load state."
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
                            selector_or_url=assertion.target or assertion.value,
                            suggested_fix="Check application logic, validation feedback, or server response."
                        ))
                        break

            if case_passed:
                passed_cases += 1

        # 6. Record unhandled JavaScript console exceptions
        if js_errors:
            for i, err in enumerate(js_errors[:5]):
                findings.append(PillarFinding(
                    id=f"JS-ERR-{i+1}",
                    title="Uncaught JavaScript exception in browser",
                    description=err,
                    severity=FindingSeverity.MEDIUM,
                    suggested_fix="Review browser console logs and add defensive null checks or try/catch blocks."
                ))

        # 7. Calculate Score (0 - 100)
        score = 100.0
        if total_cases > 0:
            score = (passed_cases / total_cases) * 80.0 + 20.0

        # Deductions for broken links and JS errors
        link_deductions = len([f for f in link_findings if f.severity == FindingSeverity.HIGH]) * 10.0 + \
                          len([f for f in link_findings if f.severity == FindingSeverity.MEDIUM]) * 5.0
        score = max(0.0, score - link_deductions)

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
                "forms_discovered": len(forms),
                "broken_links_count": len(link_findings),
                "js_errors_count": len(js_errors)
            },
            duration_sec=round(duration, 2)
        )
