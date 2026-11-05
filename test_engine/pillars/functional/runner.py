import asyncio
import hashlib
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from playwright.async_api import Page

from ai_engine.prompts.test_generation import AITestCaseGenerator
from app.common.constants import FindingSeverity, PillarType
from test_engine.browser.action_runner import ActionRunner
from test_engine.browser.assertion_runner import AssertionRunner
from test_engine.browser.evidence import EvidenceCollector
from test_engine.browser.page_summary import PageSummaryExtractor
from test_engine.browser.readiness import wait_for_page_ready
from test_engine.pillars.base import BasePillarScanner, PillarFinding, PillarScanResult
from test_engine.pillars.functional.spider import FunctionalSpider
from test_engine.policy.risk_checker import RiskPolicyChecker
from test_engine.schema.actions import ActionStep, ActionType
from test_engine.schema.assertions import AssertionType

# Playwright's "networkidle" fires after 500 ms with no network traffic; that quiet window is
# subtracted so response_time_less_than measures the actual response.
NETWORK_IDLE_QUIET_MS = 500
from test_engine.schema.case import TestCaseModel
from test_engine.crawl.site_crawler import CrawlResult, redact_text, redact_url
from test_engine.scoring.rubrics import calculate_pillar_deductions
from test_engine.pillars.functional.combinatorial import (
    discover_interaction_factors,
    estimate_case_count,
    execute_interaction_matrix,
)

logger = logging.getLogger("test_engine.pillars.functional.runner")
MAX_REACT_ACTIONS = 25  # default; overridable per-scan via kwargs["max_react_actions"]


def _classify_test_type(case: "TestCaseModel") -> str:
    tags = {t.lower() for t in (case.tags or [])}
    if any(t in tags for t in ("crud", "create", "update", "delete")): return "CRUD Testing"
    if "search" in tags: return "Search Testing"
    if "filter" in tags: return "Filter Testing"
    if "boundary" in tags or "long_input" in tags: return "Boundary Value Testing"
    if "negative" in tags: return "Negative Testing"
    if "validation" in tags or "email" in tags: return "Data Validation Testing"
    risk_val = case.risk.value if hasattr(case.risk, "value") else str(case.risk)
    if risk_val == "state_changing": return "CRUD Testing"
    return "Functional Testing"


def _next_safe_catalog_action(catalog: List[Dict[str, Any]], history: List[Dict[str, Any]]) -> Optional["ActionStep"]:
    """Choose a conservative search, dropdown, or filter action if the model stops early."""
    attempted = {item.get("target") for item in history if item.get("target")}
    candidates = []
    for item in catalog:
        selector = item.get("selector")
        actions = set(item.get("allowed_actions") or [])
        if not selector or selector in attempted:
            continue
        role = str(item.get("role") or "").lower()
        tag = str(item.get("tag") or "").lower()
        kind = str(item.get("type") or "").lower()
        name = " ".join((str(item.get("name") or ""), str(item.get("placeholder") or ""))).lower()
        if kind == "select" and "select_option" in actions:
            selected = str(item.get("selected_value") or "")
            option = next((
                option for option in item.get("options", [])
                if str(option.get("value") or "").strip()
                and str(option.get("value") or "") != selected
            ), None)
            if option:
                candidates.append((1, ActionStep(
                    action=ActionType.SELECT_OPTION,
                    target=selector,
                    value=str(option.get("label") or option.get("value")),
                    description="Select a non-current dropdown option",
                )))
        elif role == "combobox" and "click" in actions:
            candidates.append((1, ActionStep(action=ActionType.CLICK, target=selector)))
        elif "type_text" in actions and (
            role == "searchbox" or kind == "search" or re.search(r"search|filter", name)
        ):
            candidates.append((0, ActionStep(
                action=ActionType.TYPE_TEXT,
                target=selector,
                value="road",
                description="Enter a safe search term",
            )))
        elif role in ("checkbox", "radio", "tab", "menuitem") and "click" in actions:
            candidates.append((2, ActionStep(action=ActionType.CLICK, target=selector)))
        elif role == "button" and "click" in actions and re.search(
            r"search|filter|clear|reset|apply|sort|next|previous|expand|collapse|menu|view|open",
            name,
        ):
            candidates.append((3, ActionStep(action=ActionType.CLICK, target=selector)))
    return min(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


async def _emit_progress(run_id: str, stage: str, message: str, progress: int, level: str = "INFO") -> None:
    """Safely emit execution telemetry to Redis without coupling test_engine to external systems."""
    try:
        from app.common.progress import ScanProgressTracker
        await ScanProgressTracker.log(run_id, stage, message, progress, level)
    except Exception:
        pass


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

        await wait_for_page_ready(page)
        link_findings = await FunctionalSpider.audit_links_and_images(
            page, target_url, check_internal_links=crawl_result is None
        )
        findings.extend(link_findings)

        inventory = await FunctionalSpider.extract_interactive_inventory(page)
        forms = inventory.get("forms", [])
        catalog = inventory.get("elements", [])
        # Human test pack mode (use_ai=False): run only the human-written cases, even if
        # the pack is empty; no auto-generated form checks and no AI journey.
        use_ai = bool(kwargs.get("use_ai", True))
        ai_test_goals = str(kwargs.get("ai_test_goals") or "")
        max_react_actions = int(kwargs.get("max_react_actions") or MAX_REACT_ACTIONS)
        supplied_cases = bool(test_cases) or not use_ai
        cases = list(test_cases or [])
        exhaustive_mode = bool(kwargs.get("exhaustive_interactions", False))
        if not supplied_cases and not exhaustive_mode:
            suite_started = time.perf_counter()
            cases = await AITestCaseGenerator.generate_suite_for_forms(forms, target_url)
            logger.info(
                "[run %s] Form test cases (built-in rules, no AI): %d case(s) written in %.2fs",
                run_id, len(cases), time.perf_counter() - suite_started,
            )
            if forms:
                await _emit_progress(run_id, "FUNCTIONAL", f"Extracted {len(forms)} form(s) from page and synthesized {len(cases)} test scenario(s)", 27)

        allow_state_changing = bool(kwargs.get("allow_state_changing", False))
        executed_cases = 0
        passed_cases = 0
        skipped_cases = 0
        downloaded_files: List[Dict[str, Any]] = []
        visual_comparison_paths: List[str] = []
        executed_case_records: List[Dict[str, Any]] = []
        combinatorial_results = kwargs.get("precomputed_combinatorial_results") or []
        combinatorial_planned = sum(int(item.get("planned_cases") or 0) for item in combinatorial_results)
        combinatorial_executed = sum(int(item.get("executed_cases") or 0) for item in combinatorial_results)
        combinatorial_pending = sum(int(item.get("pending_cases") or 0) for item in combinatorial_results)
        combinatorial_blocked = sum(int(item.get("blocked_cases") or 0) for item in combinatorial_results)
        combinatorial_complete = not exhaustive_mode or (
            bool(combinatorial_results) and all(bool(item.get("complete")) for item in combinatorial_results)
        )
        seen_matrix_failures = set()
        for page_matrix in combinatorial_results:
            for case in page_matrix.get("test_cases", []):
                executed_case_records.append(case)
                executed_cases += 1
                if case.get("passed"):
                    passed_cases += 1
                else:
                    failed_step = next(
                        (step for step in case.get("steps", []) if step.get("status") in {"FAILED", "BLOCKED"}),
                        {},
                    )
                    failure_target = failed_step.get("target") or case.get("page_url") or target_url
                    failure_action = failed_step.get("action") or "page_reset"
                    failure_key = f"{case.get('page_url') or target_url}:{failure_action}:{failure_target}"
                    if failure_key in seen_matrix_failures:
                        continue
                    seen_matrix_failures.add(failure_key)
                    rule_suffix = hashlib.sha1(failure_key.encode("utf-8")).hexdigest()[:12]
                    blocked = case.get("status") == "BLOCKED"
                    findings.append(PillarFinding(
                        id=f"FUNC-MATRIX-{failure_action.upper()}-{rule_suffix}",
                        title="Exhaustive interaction could not be completed" if blocked else "Control failed in exhaustive interaction testing",
                        description=redact_text(str(failed_step.get("error") or case.get("actual_result") or "A discovered control combination did not complete.")),
                        severity=FindingSeverity.MEDIUM if blocked else FindingSeverity.HIGH,
                        evidence_path=case.get("evidence_path"),
                        selector_or_url=failure_target,
                        suggested_fix="Check the selected page control and repeat the case after fixing the interaction failure.",
                    ))
            if page_matrix.get("interruption_reason") and not page_matrix.get("complete"):
                findings.append(PillarFinding(
                    id=f"FUNC-MATRIX-INCOMPLETE-{len(findings) + 1}",
                    title="Exhaustive interaction coverage is incomplete",
                    description=redact_text(
                        f"{page_matrix.get('pending_cases', 0)} of {page_matrix.get('planned_cases', 0)} planned cases remain. "
                        f"{page_matrix.get('interruption_reason')}"
                    ),
                    severity=FindingSeverity.MEDIUM,
                    selector_or_url=page_matrix.get("page_url"),
                    suggested_fix="Raise the case limit or scan timeout, or reduce the finite test values and enabled permutations, then run the module again.",
                ))

        for case_idx, case in enumerate(cases):
            permitted, _ = RiskPolicyChecker.is_execution_permitted(case, allow_state_changing)
            if not permitted:
                skipped_cases += 1
                await _emit_progress(run_id, "FUNCTIONAL", f"Skipping state-changing case [{case.id}] (read-only mode)", 28)
                continue

            executed_cases += 1
            case_passed = True
            case_server_error_start = len(server_error_statuses)
            await _emit_progress(run_id, "FUNCTIONAL", f"Executing Test Scenario ({case_idx + 1}/{len(cases)}): {case.title}", 28 + min(12, case_idx * 3))

            step_records: List[Dict[str, Any]] = []
            last_action_started: Optional[float] = None  # for response_time_less_than
            for step_idx, step in enumerate(case.steps):
                tgt_info = f" on '{step.target}'" if step.target else ""
                val_info = f" with '{step.value}'" if step.value else ""
                await _emit_progress(run_id, "FUNCTIONAL", f"  ↳ [Step {step_idx + 1}/{len(case.steps)}] {step.action.value}{tgt_info}{val_info}", 28 + min(12, case_idx * 3))
                if not visual_comparison_paths and step.action != ActionType.OPEN_PAGE:
                    before_path, _ = await EvidenceCollector.capture_screenshot_bytes(
                        page, run_id, f"{case.id}-BEFORE", full_page=False
                    )
                    visual_comparison_paths.append(before_path)
                if step.action != ActionType.WAIT:
                    last_action_started = time.perf_counter()
                step_result = await ActionRunner.execute_step(page, step)
                step_records.append({
                    "action": step.action.value,
                    "target": step.target,
                    "value": step.value,
                    "status": step_result.get("status", "PASSED"),
                    "error": step_result.get("error"),
                })
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
                    await _emit_progress(run_id, "FUNCTIONAL", f"  ✗ Action '{step.action.value}' failed: {step_result.get('error')}", 30, level="WARNING")
                    break

            response_time_ms: Optional[float] = None
            response_limits = [
                float(a.value) for a in case.assertions if a.type == AssertionType.RESPONSE_TIME_LESS_THAN
            ]
            if case_passed and response_limits and last_action_started is not None:
                # Response time = from the start of the last action until the network goes quiet.
                wait_ms = min(30000, max(5000, max(response_limits) * 2))
                try:
                    await page.wait_for_load_state("networkidle", timeout=wait_ms)
                    quiet_ms = NETWORK_IDLE_QUIET_MS
                except Exception:
                    quiet_ms = 0  # still busy after wait_ms, so the response is at least that slow
                elapsed_ms = (time.perf_counter() - last_action_started) * 1000
                response_time_ms = max(0.0, elapsed_ms - quiet_ms)

            if case_passed:
                for assertion in case.assertions:
                    result = await AssertionRunner.evaluate_assertion(
                        page,
                        assertion,
                        last_status_code=last_status_code[0],
                        server_error_statuses=server_error_statuses[case_server_error_start:],
                        response_time_ms=response_time_ms,
                    )
                    assert_status = "PASSED" if result.get("passed", False) else "FAILED"
                    await _emit_progress(run_id, "FUNCTIONAL", f"  ↳ [Assert] {assertion.type.value} -> {assert_status}", 34)
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
                await _emit_progress(run_id, "FUNCTIONAL", f"  ✓ Scenario [{case.id}] passed", 36)

            executed_case_records.append({
                "id": case.id,
                "title": case.title,
                "module": next((t for t in (case.tags or []) if t.lower() not in ("boundary", "long_input", "email", "empty_required")), "General"),
                "test_type": _classify_test_type(case),
                "test_scenario": case.title,
                "expected_result": "; ".join(a.type.value for a in case.assertions) if case.assertions else "No errors",
                "actual_result": "PASSED" if case_passed else (step_records[-1].get("error") or "Assertion failed") if step_records else "Assertion failed",
                "pillar": case.pillar.value if hasattr(case.pillar, "value") else str(case.pillar),
                "risk": case.risk.value if hasattr(case.risk, "value") else str(case.risk),
                "steps": step_records,
                "assertions_count": len(case.assertions),
                "passed": case_passed,
            })

        react_actions = 0
        react_completed = False
        react_counted = False
        react_counted_cases = 0
        precomputed_ai_journeys = kwargs.get("precomputed_ai_journeys") or []
        if precomputed_ai_journeys:
            react_completed = all(bool(item.get("completed")) for item in precomputed_ai_journeys)
            for journey_index, journey in enumerate(precomputed_ai_journeys, start=1):
                findings.extend(journey.get("findings", []))
                actions = int(journey.get("actions") or 0)
                react_actions += actions
                if not visual_comparison_paths and len(journey.get("visual_paths", [])) == 2:
                    visual_comparison_paths = journey["visual_paths"]
                if not journey.get("counted"):
                    continue
                react_counted = True
                react_counted_cases += 1
                executed_cases += 1
                if journey.get("passed"):
                    passed_cases += 1
                page_label = journey.get("page_url") or redact_url(target_url)
                step_records = [
                    {
                        "action": step.get("action"),
                        "target": step.get("target"),
                        "status": step.get("status", "PASSED"),
                        "error": step.get("error"),
                    }
                    for step in journey.get("history", [])
                ]
                executed_case_records.append({
                    "id": f"AI-REACT-PAGE-{journey_index}",
                    "title": f"AI Exploratory Journey ({actions} action(s)) — {page_label}",
                    "module": "AI Exploration",
                    "test_type": "Functional Testing",
                    "test_scenario": f"Explore interactive controls on {page_label}",
                    "expected_result": "Visible search, dropdown, filter, and other safe controls respond correctly",
                    "actual_result": "PASSED" if journey.get("passed") else "FAILED",
                    "page_url": page_label,
                    "pillar": "functional",
                    "risk": "read_only",
                    "steps": step_records,
                    "assertions_count": 0,
                    "passed": bool(journey.get("passed")),
                })
        # Always run the ReAct journey in AI mode when the page has interactive elements.
        # (supplied_cases only gates the form-pack loop above, not the AI exploration.)
        if use_ai and catalog and not exhaustive_mode and not kwargs.get("skip_ai_journey", False):
            try:
                # Navigate back to the target and wait for the SPA to fully hydrate before
                # starting the ReAct journey. domcontentloaded fires before React renders
                # its components; networkidle + an extra pause ensures the interactive
                # catalog will be populated when we extract it on the first iteration.
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                journey_started = time.perf_counter()
                react_result = await self._run_react_journey(
                    page=page,
                    target_url=target_url,
                    run_id=run_id,
                    last_status_code=last_status_code,
                    server_error_statuses=server_error_statuses,
                    ai_test_goals=ai_test_goals,
                    max_react_actions=max_react_actions,
                )
                journey_seconds = time.perf_counter() - journey_started
                ai_seconds = react_result.get("ai_seconds", 0.0)
                journey_msg = (
                    f"AI test case timing: whole AI journey took {journey_seconds:.1f}s "
                    f"({ai_seconds:.1f}s waiting for the AI, {react_result.get('ai_calls', 0)} AI call(s), "
                    f"{react_result['actions']} action(s) done, completed={react_result['completed']})"
                )
                logger.info("[run %s] %s", run_id, journey_msg)
                await _emit_progress(run_id, "FUNCTIONAL", journey_msg, 38)
                react_actions = react_result["actions"]
                react_completed = react_result["completed"]
                react_counted = react_result["counted"]
                react_counted_cases = int(bool(react_result["counted"]))
                react_visual_paths = react_result.get("visual_paths", [])
                if len(react_visual_paths) == 2:
                    visual_comparison_paths = react_visual_paths
                findings.extend(react_result["findings"])
                if react_result["counted"]:
                    executed_cases += 1
                    if react_result["passed"]:
                        passed_cases += 1
                    executed_case_records.append({
                        "id": "AI-REACT-JOURNEY",
                        "title": f"AI Exploratory Journey ({react_actions} action(s))",
                        "module": "AI Exploration",
                        "test_type": "Functional Testing",
                        "test_scenario": f"AI agent explored page: {react_actions} action(s). Goal: {ai_test_goals or 'general exploration'}",
                        "expected_result": "Page responds correctly to AI-driven interactions",
                        "actual_result": "PASSED" if react_result.get("passed") else "FAILED",
                        "pillar": "functional",
                        "risk": "read_only",
                        "steps": [
                            {
                                "action": step.get("action"),
                                "target": step.get("target"),
                                "status": step.get("status", "PASSED"),
                                "error": step.get("error"),
                            }
                            for step in react_result.get("history", [])
                        ],
                        "assertions_count": 0,
                        "passed": react_result.get("passed", False),
                    })
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
                if (crawled_page.page_observations or {}).get("interactive_ready") is False:
                    site_findings.append(PillarFinding(
                        id="FUNC-PAGE-REMAINS-LOADING",
                        title="Interactive testing was blocked by a page loading state",
                        description=f"The page at {path} still showed a loading overlay or busy indicator after the bounded readiness wait, so its controls could not be tested reliably.",
                        severity=FindingSeverity.MEDIUM,
                        selector_or_url=redact_url(page_url),
                        suggested_fix="Check the page's loading overlay and the API requests that populate its controls.",
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
            or (crawl_result.skipped_depth_limit and crawl_result.max_depth_limit > 0)
            or crawl_result.stopped_after_incomplete_page
            or crawl_result.skipped_unsafe
        ))
        crawl_errors = bool(crawl_result and any(item.navigation_error for item in crawl_result.pages))
        finding_severity_counts = {
            member.value: sum(finding.severity == member for finding in findings)
            for member in FindingSeverity
        }
        if score < 60 or finding_severity_counts[FindingSeverity.CRITICAL.value]:
            status = "FAILED"
        elif score < 85 or finding_severity_counts[FindingSeverity.HIGH.value] or not executed_cases or crawl_limited or crawl_errors or not combinatorial_complete:
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
                "total_cases": len(cases) + react_counted_cases + combinatorial_planned,
                "executed_cases": executed_cases,
                "passed_cases": passed_cases,
                "failed_cases": executed_cases - passed_cases,
                "skipped_cases": skipped_cases,
                "blocked_cases": combinatorial_blocked,
                "planned_combinatorial_cases": combinatorial_planned,
                "executed_combinatorial_cases": combinatorial_executed,
                "passed_combinatorial_cases": sum(int(item.get("passed_cases") or 0) for item in combinatorial_results),
                "failed_combinatorial_cases": sum(int(item.get("failed_cases") or 0) for item in combinatorial_results),
                "pending_combinatorial_cases": combinatorial_pending,
                "combinatorial_coverage_complete": combinatorial_complete,
                "combinatorial_pages": [
                    {key: value for key, value in item.items() if key != "test_cases"}
                    for item in combinatorial_results
                ],
                "executed_test_cases": executed_case_records,
                "forms_discovered": len(forms),
                "interactive_elements_discovered": len(catalog),
                "react_actions": react_actions,
                "react_completed": react_completed,
                "interaction_coverage_mode": (
                    "exhaustive_cartesian" if exhaustive_mode else "ai_exploratory" if use_ai else "authored_cases"
                ),
                "page_interaction_journeys": len(precomputed_ai_journeys),
                "pages_with_interaction_actions": sum(
                    int(item.get("actions") or 0) > 0 for item in precomputed_ai_journeys
                ),
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

    async def explore_crawled_page(
        self,
        *,
        page: Page,
        target_url: str,
        run_id: str,
        initial_status_code: Optional[int] = None,
        page_ready: bool = True,
        ai_test_goals: str = "",
        max_react_actions: int = MAX_REACT_ACTIONS,
        cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Dict[str, Any]:
        """Interact with a page while it is current, before the crawler follows its links."""
        if not page_ready:
            return {
                "actions": 0,
                "completed": False,
                "counted": False,
                "passed": False,
                "findings": [],
                "history": [],
                "visual_paths": [],
                "ai_seconds": 0.0,
                "ai_calls": 0,
                "page_url": redact_url(target_url),
            }

        inventory = await FunctionalSpider.extract_interactive_inventory(page)
        has_safe_controls = any(
            set(item.get("allowed_actions") or []) & {"click", "select_option", "type_text"}
            for item in inventory.get("elements", [])
        )
        if not has_safe_controls:
            return {
                "actions": 0,
                "completed": True,
                "counted": False,
                "passed": True,
                "findings": [],
                "history": [],
                "visual_paths": [],
                "ai_seconds": 0.0,
                "ai_calls": 0,
                "page_url": redact_url(target_url),
            }

        result = await self._run_react_journey(
            page=page,
            target_url=target_url,
            run_id=run_id,
            last_status_code=[initial_status_code],
            server_error_statuses=[],
            ai_test_goals=ai_test_goals,
            max_react_actions=max_react_actions,
            cancellation_check=cancellation_check,
        )
        result["page_url"] = redact_url(target_url)
        return result

    async def test_crawled_page_combinations(
        self,
        *,
        page: Page,
        target_url: str,
        run_id: str,
        text_values: Optional[List[str]] = None,
        max_generated_cases: int = 1000,
        include_action_order_permutations: bool = False,
        resume_state: Optional[Dict[str, Any]] = None,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        deadline: Optional[float] = None,
        cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Dict[str, Any]:
        """Discover the page's finite factors and run their full Cartesian suite."""
        discovery = await discover_interaction_factors(
            page,
            target_url=target_url,
            text_values=text_values or [],
            deadline=deadline,
            cancellation_check=cancellation_check,
        )
        if not discovery.get("discovery_complete", True):
            planned = estimate_case_count(discovery["factors"], include_action_order_permutations)
            result = {
                "mode": "exhaustive_cartesian",
                "page_url": redact_url(target_url),
                "factors": [
                    {"id": factor["id"], "label": factor["label"], "kind": factor["kind"], "values": [value["label"] for value in factor["values"]]}
                    for factor in discovery["factors"]
                ],
                "factors_discovered": len(discovery["factors"]),
                "planned_cases": planned,
                "executed_cases": 0,
                "passed_cases": 0,
                "failed_cases": 0,
                "blocked_cases": 0,
                "pending_cases": planned,
                "case_limit": max_generated_cases,
                "limit_exceeded": False,
                "action_order_permutations": include_action_order_permutations,
                "complete": False,
                "interruption_reason": discovery["discovery_errors"][0],
                "test_cases": [],
                "dynamic_controls": [],
            }
        else:
            result = await execute_interaction_matrix(
                page,
                target_url=target_url,
                run_id=run_id,
                factors=discovery["factors"],
                max_generated_cases=max_generated_cases,
                include_action_order_permutations=include_action_order_permutations,
                baseline_controls=discovery.get("control_snapshot"),
                resume_state=resume_state,
                checkpoint_callback=checkpoint_callback,
                deadline=deadline,
                cancellation_check=cancellation_check,
            )
        result.update({key: value for key, value in discovery.items() if key not in {"factors", "control_snapshot"}})
        result["discovery_errors"] = discovery.get("discovery_errors", [])
        if discovery.get("undiscovered_custom_dropdowns"):
            result["complete"] = False
            result["interruption_reason"] = (
                f"{discovery['undiscovered_custom_dropdowns']} custom dropdown(s) opened without exposing "
                "accessible option values; their option domains could not be exhaustively enumerated."
            )
        if discovery.get("discovery_errors"):
            result["complete"] = False
            errors = "; ".join(discovery["discovery_errors"][:3])
            result["interruption_reason"] = "; ".join(filter(None, [result.get("interruption_reason"), errors]))
        if discovery.get("unsupported_controls"):
            result["complete"] = False
            unsupported = "; ".join(
                f"{item['label']}: {item['reason']}"
                for item in discovery["unsupported_controls"][:3]
            )
            result["interruption_reason"] = "; ".join(filter(None, [
                result.get("interruption_reason"),
                f"Some control domains could not be enumerated: {unsupported}.",
            ]))
        return result

    async def _run_react_journey(
        self,
        *,
        page: Page,
        target_url: str,
        run_id: str,
        last_status_code: List[Optional[int]],
        server_error_statuses: List[int],
        ai_test_goals: str = "",
        max_react_actions: int = MAX_REACT_ACTIONS,
        cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> Dict[str, Any]:
        history: List[Dict[str, Any]] = []
        findings: List[PillarFinding] = []
        visual_paths: List[str] = []
        server_error_start = len(server_error_statuses)
        # AI timing totals, reported by the caller (kept in a dict so every return path shares them).
        ai_timing = {"ai_seconds": 0.0, "ai_calls": 0}

        def journey_result(*, completed: bool, counted: bool, passed: bool) -> Dict[str, Any]:
            return {
                "actions": len(history),
                "completed": completed,
                "counted": counted,
                "passed": passed,
                "findings": findings,
                "visual_paths": visual_paths,
                "history": list(history),
                **ai_timing,
            }

        def rejected_step(reason: str) -> Dict[str, Any]:
            findings.append(PillarFinding(
                id="FUNC-REACT-REJECTED-STEP",
                title="AI interaction proposed an unsupported action",
                description=reason,
                severity=FindingSeverity.MEDIUM,
            ))
            return journey_result(completed=False, counted=True, passed=False)

        # Each callback owns only its current route. Linked pages are tested later by
        # the crawler, after this route's interaction pass has completed.
        _target_parts = urlsplit(target_url)
        target_netloc = _target_parts.netloc
        target_path = _target_parts.path.rstrip("/") or "/"

        for iteration in range(max_react_actions + 1):
            if cancellation_check and await cancellation_check():
                raise asyncio.CancelledError("Scan cancelled during AI page interaction")
            # Brief pause on every iteration so React/Angular components finish rendering
            # after any action or navigation that may have triggered a re-render.
            await page.wait_for_timeout(800)
            summary = await PageSummaryExtractor.extract_summary(page)
            catalog = summary.get("interactive_elements", [])
            logger.info(
                f"[run {run_id}] AI ReAct exploration (step {iteration + 1}/{max_react_actions}): "
                f"analyzing {len(catalog)} interactive elements on {page.url!r} via LLM..."
            )
            step_started = time.perf_counter()
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
                ai_test_goals=ai_test_goals,
            )
            step_seconds = time.perf_counter() - step_started
            ai_timing["ai_seconds"] += step_seconds
            ai_timing["ai_calls"] += 1
            forced_step = None
            if decision is None or decision.done:
                # The controller enforces useful coverage even if the model answers "done"
                # before trying the page's safe search, dropdown, or filter controls.
                forced_step = _next_safe_catalog_action(catalog, history)
            if forced_step is not None:
                outcome = f"safe fallback action '{forced_step.action.value}'"
            elif decision is None:
                outcome = "no valid answer"
            elif decision.done:
                outcome = f"finished with {len(decision.assertions)} assertion(s)"
            else:
                outcome = f"next action '{decision.step.action.value}'"
            step_msg = f"AI test case timing: step {iteration + 1} took {step_seconds:.1f}s ({outcome})"
            logger.info("[run %s] %s", run_id, step_msg)
            await _emit_progress(run_id, "FUNCTIONAL", step_msg, 30)

            if decision is None and forced_step is None:
                findings.append(PillarFinding(
                    id="FUNC-REACT-INCOMPLETE",
                    title="AI interaction could not produce a verifiable result",
                    description="The bounded interaction stopped before producing a validated assertion.",
                    severity=FindingSeverity.MEDIUM,
                ))
                return journey_result(completed=False, counted=True, passed=False)

            if decision is not None and decision.done and forced_step is None:
                if not history:
                    findings.append(PillarFinding(
                        id="FUNC-REACT-NO-ACTION",
                        title="AI interaction finished without testing an interaction",
                        description="The agent returned an assertion before performing any allowed page action.",
                        severity=FindingSeverity.MEDIUM,
                    ))
                    return journey_result(completed=False, counted=True, passed=False)
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
                        return journey_result(completed=False, counted=True, passed=False)
                passed = not any(item.get("status") == "FAILED" for item in history)
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
                return journey_result(completed=True, counted=True, passed=passed)

            if len(history) >= max_react_actions:
                findings.append(PillarFinding(
                    id="FUNC-REACT-STEP-LIMIT",
                    title="AI interaction reached its action limit",
                    description=f"The journey did not finish or assert a result within {max_react_actions} actions.",
                    severity=FindingSeverity.MEDIUM,
                ))
                return journey_result(completed=False, counted=True, passed=False)

            step = forced_step or (decision.step if decision is not None else None)
            if step is None:
                return rejected_step("The AI did not provide an action and no safe fallback action was available.")
            if step.target and any(item.get("target") == step.target for item in history):
                fallback = _next_safe_catalog_action(catalog, history)
                if fallback:
                    step = fallback
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
                    fallback = _next_safe_catalog_action(catalog, history)
                    if not fallback:
                        return rejected_step("The target or action was not allowed by the current interactive catalog.")
                    step = fallback
                    entry = next((item for item in catalog if item["selector"] == step.target), None)
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
                "error": redact_text(result.get("error", "")) if result.get("error") else None,
            })

            # URL boundary guard: if an interaction navigates away from this page route,
            # return here and leave linked-route navigation to the crawler.
            current_parts = urlsplit(page.url)
            current_path = current_parts.path.rstrip("/") or "/"
            if (current_parts.netloc != target_netloc or
                    current_path != target_path):
                logger.info(
                    f"[run {run_id}] ReAct drifted to {page.url!r} (outside {target_url!r}), navigating back"
                )
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass

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
                # Mark this selector as attempted and continue through the rest of the
                # page instead of ending the whole run on one unusable control.

        findings.append(PillarFinding(
            id="FUNC-REACT-STEP-LIMIT",
            title="AI interaction reached its action limit",
            description=f"The page still had untested safe controls after {max_react_actions} actions.",
            severity=FindingSeverity.MEDIUM,
        ))
        return journey_result(completed=False, counted=True, passed=False)
