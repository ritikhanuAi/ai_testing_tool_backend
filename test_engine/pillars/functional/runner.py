import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from playwright.async_api import Page

from ai_engine.prompts.test_generation import AITestCaseGenerator
from app.common.constants import FindingSeverity, PillarType
from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.assertion_runner import AssertionRunner
from test_engine.browser.evidence import EvidenceCollector
from test_engine.browser.page_summary import PageSummaryExtractor
from test_engine.pillars.base import BasePillarScanner, PillarFinding, PillarScanResult
from test_engine.pillars.functional.spider import FunctionalSpider
from test_engine.policy.risk_checker import RiskPolicyChecker
from test_engine.schema.actions import ActionType
from test_engine.schema.assertions import AssertionType
from test_engine.schema.case import TestCaseModel
from test_engine.crawl.site_crawler import CrawlResult, redact_text, redact_url
from test_engine.scoring.rubrics import calculate_pillar_deductions

logger = logging.getLogger("test_engine.pillars.functional.runner")
MAX_REACT_ACTIONS = 5


class FunctionalScanner(BasePillarScanner):
    """Run authored or deterministic checks, then a bounded observe-act-observe journey."""

    @property
    def pillar_type(self) -> PillarType:
        return PillarType.FUNCTIONAL

    async def scan(
        self,
        page: Page,
        target_url: str,
        test_cases: Optional[List[TestCaseModel]] = None,
        run_id: str = "manual_run",
        crawl_result: Optional[CrawlResult] = None,
        initial_status_code: Optional[int] = None,
        **kwargs,
    ) -> PillarScanResult:
        start_time = time.time()
        findings: List[PillarFinding] = []
        js_errors: List[str] = []
        last_status_code: List[Optional[int]] = [initial_status_code]
        server_error_statuses: List[int] = []
        page.on("pageerror", lambda error: js_errors.append(redact_text(str(error))))

        def record_navigation_status(response):
            if response.request.is_navigation_request():
                last_status_code[0] = response.status
            if response.status >= 500:
                server_error_statuses.append(response.status)

        page.on("response", record_navigation_status)

        link_findings = await FunctionalSpider.audit_links_and_images(
            page, target_url, check_internal_links=crawl_result is None
        )
        findings.extend(link_findings)

        inventory = await FunctionalSpider.extract_interactive_inventory(page)
        forms = inventory.get("forms", [])
        catalog = inventory.get("elements", [])
        supplied_cases = bool(test_cases)
        cases = list(test_cases or [])
        if not supplied_cases:
            cases = await AITestCaseGenerator.generate_suite_for_forms(forms, target_url)

        allow_state_changing = bool(kwargs.get("allow_state_changing", False))
        executed_cases = 0
        passed_cases = 0
        skipped_cases = 0
        downloaded_files: List[Dict[str, Any]] = []
        visual_comparison_paths: List[str] = []

        for case in cases:
            permitted, _ = RiskPolicyChecker.is_execution_permitted(case, allow_state_changing)
            if not permitted:
                skipped_cases += 1
                continue

            executed_cases += 1
            case_passed = True
            case_server_error_start = len(server_error_statuses)
            for step in case.steps:
                if not visual_comparison_paths and step.action != ActionType.OPEN_PAGE:
                    before_path, _ = await EvidenceCollector.capture_screenshot_bytes(
                        page, run_id, f"{case.id}-BEFORE", full_page=False
                    )
                    visual_comparison_paths.append(before_path)
                step_result = await ActionRunner.execute_step(page, step)
                if step_result.get("download_path"):
                    downloaded_files.append({
                        "test_case_id": case.id,
                        "path": step_result["download_path"],
                        "size_bytes": step_result["size_bytes"],
                    })
                if len(visual_comparison_paths) == 1 and step.action != ActionType.OPEN_PAGE:
                    after_path, _ = await EvidenceCollector.capture_screenshot_bytes(
                        page, run_id, f"{case.id}-AFTER", full_page=False
                    )
                    visual_comparison_paths.append(after_path)
                if step_result["status"] == "FAILED":
                    case_passed = False
                    screenshot_path = await EvidenceCollector.capture_screenshot(page, run_id, case.id)
                    findings.append(PillarFinding(
                        id=f"FUNC-FAIL-{case.id}",
                        title=f"Step failed in {case.title}",
                        description=redact_text(f"Action '{step.action.value}' on '{step.target}' failed: {step_result.get('error')}"),
                        severity=FindingSeverity.HIGH,
                        evidence_path=screenshot_path,
                        selector_or_url=redact_text(step.target or ""),
                        suggested_fix="Check the accessible control name, selector, and current page state.",
                    ))
                    break

            if case_passed:
                for assertion in case.assertions:
                    result = await AssertionRunner.evaluate_assertion(
                        page,
                        assertion,
                        last_status_code=last_status_code[0],
                        server_error_statuses=server_error_statuses[case_server_error_start:],
                    )
                    if not result.get("passed", False):
                        case_passed = False
                        screenshot_path = await EvidenceCollector.capture_screenshot(page, run_id, case.id)
                        findings.append(PillarFinding(
                            id=f"ASSERT-FAIL-{case.id}",
                            title=f"Assertion failed in {case.title}",
                            description=redact_text(f"Assertion failed. Expected: {result.get('expected')}, observed: {result.get('actual')}"),
                            severity=FindingSeverity.HIGH,
                            evidence_path=screenshot_path,
                            selector_or_url=redact_text(str(assertion.target or assertion.value or "")),
                            suggested_fix="Check the application validation and visible response for this interaction.",
                        ))
                        break

            if case_passed:
                passed_cases += 1

        react_actions = 0
        react_completed = False
        react_counted = False
        if not supplied_cases and catalog:
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                react_result = await self._run_react_journey(
                    page=page,
                    target_url=target_url,
                    run_id=run_id,
                    last_status_code=last_status_code,
                    server_error_statuses=server_error_statuses,
                )
                react_actions = react_result["actions"]
                react_completed = react_result["completed"]
                react_counted = react_result["counted"]
                react_visual_paths = react_result.get("visual_paths", [])
                if len(react_visual_paths) == 2:
                    visual_comparison_paths = react_visual_paths
                findings.extend(react_result["findings"])
                if react_result["counted"]:
                    executed_cases += 1
                    if react_result["passed"]:
                        passed_cases += 1
            except Exception as error:
                logger.warning("Bounded AI journey stopped: %s", redact_text(str(error)))

        if js_errors:
            for index, error in enumerate(js_errors[:5]):
                findings.append(PillarFinding(
                    id=f"JS-ERR-{index + 1}",
                    title="Uncaught JavaScript exception in browser",
                    description=error,
                    severity=FindingSeverity.MEDIUM,
                    suggested_fix="Review the browser console error and add defensive handling.",
                ))

        journey_score = 100.0
        if executed_cases:
            journey_score = (passed_cases / executed_cases) * 80.0 + 20.0
        else:
            findings.append(PillarFinding(
                id="FUNC-NO-INTERACTION-CASES-EXECUTED",
                title="No functional interaction case completed",
                description="The report can grade route health, but it has no executed interaction case to grade user workflows.",
                severity=FindingSeverity.INFO,
                selector_or_url=redact_url(target_url),
                suggested_fix="Add an authorized test pack for the important user journeys or review the route-health findings separately.",
            ))
        link_deductions = (
            len([finding for finding in link_findings if finding.severity == FindingSeverity.HIGH]) * 10.0
            + len([finding for finding in link_findings if finding.severity == FindingSeverity.MEDIUM]) * 5.0
        )
        journey_score = max(0.0, journey_score - link_deductions - min(20.0, len(js_errors) * 5.0))

        site_findings: List[PillarFinding] = []
        page_status_counts: Dict[str, int] = {}
        if crawl_result:
            for crawled_page in crawl_result.pages:
                page_url = crawled_page.final_url or crawled_page.url
                path = urlsplit(page_url).path or "/"
                status_key = str(crawled_page.status_code) if crawled_page.status_code is not None else "no_response"
                page_status_counts[status_key] = page_status_counts.get(status_key, 0) + 1
                if crawled_page.status_code is not None and crawled_page.status_code >= 400:
                    code = crawled_page.status_code
                    site_findings.append(PillarFinding(
                        id=f"FUNC-ROUTE-HTTP-{code}",
                        title=f"Crawled page returned HTTP {code}",
                        description=f"The in-scope route {path} returned HTTP {code} during the site crawl.",
                        severity=FindingSeverity.HIGH if code >= 500 else FindingSeverity.MEDIUM,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Fix the route or update links that point to a page that should no longer be available.",
                    ))
                elif crawled_page.navigation_error:
                    site_findings.append(PillarFinding(
                        id="FUNC-ROUTE-NAVIGATION-ERROR",
                        title="An in-scope page could not be loaded",
                        description=f"The site crawl could not load {path}: {crawled_page.navigation_error}",
                        severity=FindingSeverity.MEDIUM,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Check the route availability and repeat the authorized scan.",
                    ))

                observations = crawled_page.page_observations
                if observations.get("js_error_count", 0):
                    site_findings.append(PillarFinding(
                        id="FUNC-UNCAUGHT-JAVASCRIPT-ERROR",
                        title="Uncaught JavaScript exceptions on a crawled page",
                        description=f"The browser observed {observations['js_error_count']} uncaught JavaScript exception(s) on {path}.",
                        severity=FindingSeverity.MEDIUM,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Inspect the page's browser console and fix uncaught exceptions that disrupt page behavior.",
                    ))
                if observations.get("console_error_count", 0):
                    site_findings.append(PillarFinding(
                        id="FUNC-BROWSER-CONSOLE-ERROR",
                        title="Browser console errors on a crawled page",
                        description=f"The browser recorded {observations['console_error_count']} console error(s) on {path}.",
                        severity=FindingSeverity.LOW,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Review the console errors and confirm they do not indicate failed application behavior or missing assets.",
                    ))
                if observations.get("failed_request_count", 0):
                    site_findings.append(PillarFinding(
                        id="FUNC-FAILED-IN-SCOPE-REQUEST",
                        title="A page had failed in-scope network requests",
                        description=f"The browser observed {observations['failed_request_count']} failed request(s) on {path}.",
                        severity=FindingSeverity.MEDIUM,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Check the affected route's scripts, styles, and other first-party assets for missing or unreachable resources.",
                    ))
                if observations.get("subresource_http_errors"):
                    status_codes = sorted(set(observations["subresource_http_errors"]))
                    site_findings.append(PillarFinding(
                        id="FUNC-FAILED-IN-SCOPE-RESOURCE",
                        title="First-party page resources returned an error",
                        description=f"First-party resources returned HTTP status code(s) {status_codes} on {path}.",
                        severity=FindingSeverity.MEDIUM,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Repair or remove the failing first-party asset references.",
                    ))
                if observations.get("broken_images"):
                    site_findings.append(PillarFinding(
                        id="FUNC-BROKEN-IMAGE",
                        title="Broken images found on a crawled page",
                        description=f"The browser found {len(observations['broken_images'])} broken image(s) on {path}.",
                        severity=FindingSeverity.LOW,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Repair the image paths or remove references to missing assets.",
                    ))

            findings.extend(site_findings)
        site_health_score, site_deductions = calculate_pillar_deductions(site_findings)
        score = round((journey_score * 0.7) + (site_health_score * 0.3), 1) if executed_cases else site_health_score
        crawl_limited = bool(crawl_result and (
            crawl_result.truncated_by_page_limit
            or crawl_result.truncated_by_timeout
            or crawl_result.truncated_by_link_limit
            or crawl_result.sitemap_truncated
            or crawl_result.skipped_depth_limit
            or crawl_result.skipped_unsafe
        ))
        crawl_errors = bool(crawl_result and any(item.navigation_error for item in crawl_result.pages))
        finding_severity_counts = {
            member.value: sum(finding.severity == member for finding in findings)
            for member in FindingSeverity
        }
        if score < 60 or finding_severity_counts[FindingSeverity.CRITICAL.value]:
            status = "FAILED"
        elif score < 85 or finding_severity_counts[FindingSeverity.HIGH.value] or not executed_cases or crawl_limited or crawl_errors:
            status = "WARNING"
        else:
            status = "PASSED"

        duration = time.time() - start_time
        return PillarScanResult(
            pillar=self.pillar_type,
            score=round(score, 1),
            status=status,
            findings=findings,
            metrics={
                "total_cases": len(cases) + (1 if react_counted else 0),
                "executed_cases": executed_cases,
                "passed_cases": passed_cases,
                "failed_cases": executed_cases - passed_cases,
                "skipped_cases": skipped_cases,
                "forms_discovered": len(forms),
                "interactive_elements_discovered": len(catalog),
                "react_actions": react_actions,
                "react_completed": react_completed,
                "visual_comparison_paths": visual_comparison_paths,
                "downloads": downloaded_files,
                "broken_links_count": len(link_findings),
                "js_errors_count": len(js_errors),
                "site_pages_attempted": len(crawl_result.pages) if crawl_result else 0,
                "site_pages_with_navigation_errors": sum(
                    item.navigation_error is not None for item in crawl_result.pages
                ) if crawl_result else 0,
                "site_page_status_counts": page_status_counts,
                "site_health_score": site_health_score,
                "site_health_deductions": site_deductions,
                "interaction_score_available": executed_cases > 0,
                "score_scope": "interaction journeys plus site route health" if executed_cases else "site route health only; interactions ungraded",
                "authored_interaction_scope": "starting page and explicit test-pack navigation",
                "site_route_health_scope": "all pages visited by the bounded crawler",
                "crawl_coverage": crawl_result.report_summary() if crawl_result else {},
                "finding_count": len(findings),
                "finding_severity_counts": finding_severity_counts,
            },
            duration_sec=round(duration, 2),
        )

    async def _run_react_journey(
        self,
        *,
        page: Page,
        target_url: str,
        run_id: str,
        last_status_code: List[Optional[int]],
        server_error_statuses: List[int],
    ) -> Dict[str, Any]:
        history: List[Dict[str, Any]] = []
        findings: List[PillarFinding] = []
        visual_paths: List[str] = []
        server_error_start = len(server_error_statuses)

        def rejected_step(reason: str) -> Dict[str, Any]:
            findings.append(PillarFinding(
                id="FUNC-REACT-REJECTED-STEP",
                title="AI interaction proposed an unsupported action",
                description=reason,
                severity=FindingSeverity.MEDIUM,
            ))
            return {
                "actions": len(history), "completed": False, "counted": True,
                "passed": False, "findings": findings, "visual_paths": visual_paths,
            }

        for iteration in range(MAX_REACT_ACTIONS + 1):
            summary = await PageSummaryExtractor.extract_summary(page)
            catalog = summary.get("interactive_elements", [])
            logger.info(
                f"[run {run_id}] AI ReAct exploration (step {iteration + 1}/{MAX_REACT_ACTIONS}): "
                f"analyzing {len(catalog)} interactive elements on {target_url} via LLM..."
            )
            decision = await AITestCaseGenerator.choose_next_action(
                target_url=target_url,
                page_summary={
                    "title": summary.get("title"),
                    "headings": summary.get("headings", []),
                    "visible_feedback": summary.get("visible_feedback", []),
                    "forms": [
                        {"form_id": form.get("form_id"), "selector": form.get("selector")}
                        for form in summary.get("forms", [])
                    ],
                },
                catalog=catalog,
                history=history,
            )
            if decision is None:
                counted = bool(history)
                if counted:
                    findings.append(PillarFinding(
                        id="FUNC-REACT-INCOMPLETE",
                        title="AI interaction could not produce a verifiable result",
                        description="The bounded interaction stopped before producing a validated assertion.",
                        severity=FindingSeverity.MEDIUM,
                    ))
                return {"actions": len(history), "completed": False, "counted": counted, "passed": False, "findings": findings, "visual_paths": visual_paths}

            if decision.done:
                if not history:
                    findings.append(PillarFinding(
                        id="FUNC-REACT-NO-ACTION",
                        title="AI interaction finished without testing an interaction",
                        description="The agent returned an assertion before performing any allowed page action.",
                        severity=FindingSeverity.MEDIUM,
                    ))
                    return {"actions": 0, "completed": False, "counted": True, "passed": False, "findings": findings, "visual_paths": visual_paths}
                catalog_by_selector = {item["selector"]: item for item in catalog}
                form_selectors = {
                    form.get("selector") for form in summary.get("forms", []) if form.get("selector")
                }
                selectors = set(catalog_by_selector) | form_selectors
                for assertion in decision.assertions:
                    entry = catalog_by_selector.get(assertion.target)
                    invalid_target = assertion.target and assertion.target not in selectors
                    if assertion.type == AssertionType.FORM_INVALID:
                        invalid_target = assertion.target not in form_selectors
                    elif assertion.type == AssertionType.INPUT_INVALID:
                        invalid_target = not entry or entry.get("tag") not in ("input", "textarea", "select")
                    elif assertion.type in (AssertionType.OPTION_LIST_NOT_EMPTY, AssertionType.OPTION_COUNT_EQUALS):
                        invalid_target = not entry or entry.get("tag") != "select"
                    if invalid_target:
                        findings.append(PillarFinding(
                            id="FUNC-REACT-INVALID-ASSERTION",
                            title="AI interaction returned an unknown assertion target",
                            description="The assertion target was not present in the current interactive catalog.",
                            severity=FindingSeverity.MEDIUM,
                        ))
                        return {"actions": len(history), "completed": False, "counted": True, "passed": False, "findings": findings, "visual_paths": visual_paths}
                passed = True
                for assertion in decision.assertions:
                    result = await AssertionRunner.evaluate_assertion(
                        page,
                        assertion,
                        last_status_code=last_status_code[0],
                        server_error_statuses=server_error_statuses[server_error_start:],
                    )
                    if not result.get("passed", False):
                        passed = False
                        evidence = await EvidenceCollector.capture_screenshot(page, run_id, "AI-REACT")
                        findings.append(PillarFinding(
                            id=f"ASSERT-FAIL-AI-REACT-{assertion.type.value.upper()}",
                            title="AI interaction assertion failed",
                            description=redact_text(f"Assertion failed. Expected: {result.get('expected')}, observed: {result.get('actual')}"),
                            severity=FindingSeverity.HIGH,
                            evidence_path=evidence,
                            selector_or_url=redact_text(str(assertion.target or assertion.value or "")),
                        ))
                return {"actions": len(history), "completed": True, "counted": True, "passed": passed, "findings": findings, "visual_paths": visual_paths}

            if len(history) >= MAX_REACT_ACTIONS:
                findings.append(PillarFinding(
                    id="FUNC-REACT-STEP-LIMIT",
                    title="AI interaction reached its action limit",
                    description=f"The journey did not finish or assert a result within {MAX_REACT_ACTIONS} actions.",
                    severity=FindingSeverity.MEDIUM,
                ))
                return {"actions": len(history), "completed": False, "counted": True, "passed": False, "findings": findings, "visual_paths": visual_paths}

            step = decision.step
            logger.info(f"[run {run_id}] AI chose action '{step.action.value}' on target '{step.target}'")
            step = step.model_copy(update={
                "timeout_ms": min(max(step.timeout_ms or 10000, 1), 5000),
            })
            if step.action == ActionType.WAIT:
                try:
                    wait_ms = int(step.value or "500")
                except ValueError:
                    return rejected_step("The requested wait duration was not an integer.")
                if wait_ms < 0 or wait_ms > 1500:
                    return rejected_step("Waits must be between zero and 1500 milliseconds.")
                step = step.model_copy(update={"value": str(wait_ms)})
            elif step.action == ActionType.SCROLL:
                step = step.model_copy(update={"target": None})
            else:
                entry = next((item for item in catalog if item["selector"] == step.target), None)
                if not entry or step.action.value not in entry.get("allowed_actions", []):
                    return rejected_step("The target or action was not allowed by the current interactive catalog.")
                if step.action == ActionType.TYPE_TEXT and len(step.value or "") > 1000:
                    return rejected_step("Text entry is limited to 1000 characters per action.")
                if step.action == ActionType.SELECT_OPTION and not any(
                    str(step.value) in (option.get("label"), option.get("value"))
                    for option in entry.get("options", [])
                ):
                    return rejected_step("The requested select option was not present in the current page catalog.")
                step = step.model_copy(update={
                    "fallback_role": entry.get("fallback_role"),
                    "fallback_name": entry.get("fallback_name"),
                })

            if not visual_paths:
                before_path, _ = await EvidenceCollector.capture_screenshot_bytes(
                    page, run_id, "AI-REACT-BEFORE", full_page=False
                )
                visual_paths.append(before_path)
            result = await ActionRunner.execute_step(page, step)
            history.append({
                "action": step.action.value,
                "target": step.target,
                "status": result["status"],
                "healed": result.get("healed", False),
            })
            after_path, _ = await EvidenceCollector.capture_screenshot_bytes(
                page, run_id, "AI-REACT-AFTER", full_page=False
            )
            if len(visual_paths) == 1:
                visual_paths.append(after_path)
            else:
                visual_paths[1] = after_path
            if result["status"] == "FAILED":
                evidence = await EvidenceCollector.capture_screenshot(page, run_id, "AI-REACT")
                findings.append(PillarFinding(
                    id=f"FUNC-REACT-STEP-{len(history)}",
                    title="AI interaction step failed",
                    description=redact_text(result.get("error", "The selected interaction could not be completed.")),
                    severity=FindingSeverity.HIGH,
                    evidence_path=evidence,
                    selector_or_url=redact_text(step.target or ""),
                ))
                return {"actions": len(history), "completed": False, "counted": True, "passed": False, "findings": findings, "visual_paths": visual_paths}

        return {"actions": len(history), "completed": False, "counted": bool(history), "passed": False, "findings": findings, "visual_paths": visual_paths}
