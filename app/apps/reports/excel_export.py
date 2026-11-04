"""Build an Excel (.xlsx) workbook from a saved 4-pillar report."""
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.apps.reports.models import ReportSummary

PILLAR_ORDER = ["functional", "security", "performance", "accessibility"]
SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEVERITY_FILLS = {
    "critical": "F8D7DA",
    "high":     "FDE2CC",
    "medium":   "FFF3CD",
    "low":      "D1ECF1",
    "info":     "E9ECEF",
}
HEADER_FILL  = PatternFill("solid", fgColor="212121")
HEADER_FONT  = Font(bold=True, color="FFFFFF")
TITLE_FONT   = Font(bold=True, size=14)
SECTION_FONT = Font(bold=True, size=12)
WRAP         = Alignment(wrap_text=True, vertical="top")
PASS_FILL    = PatternFill("solid", fgColor="C6EFCE")
PASS_FONT    = Font(bold=True, color="276221")
FAIL_FILL    = PatternFill("solid", fgColor="FFC7CE")
FAIL_FONT    = Font(bold=True, color="9C0006")


def _clean(value: Any) -> Any:
    """Make a value safe to write into a cell."""
    if value is None:
        return ""
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value)
    elif isinstance(value, dict):
        value = "; ".join(f"{k}: {v}" for k, v in value.items())
    return ILLEGAL_CHARACTERS_RE.sub("", str(value))[:32000]


def _set(ws: Worksheet, row: int, col: int, value: Any) -> Any:
    cell = ws.cell(row=row, column=col, value=_clean(value))
    if isinstance(cell.value, str):
        cell.data_type = "s"
    return cell


def _write_table(
    ws: Worksheet,
    start_row: int,
    headers: List[str],
    rows: Iterable[List[Any]],
    severity_col: Optional[int] = None,
) -> int:
    """Write a header row plus data rows; return the next free row."""
    for col, header in enumerate(headers, start=1):
        cell = _set(ws, start_row, col, header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    row = start_row
    for values in rows:
        row += 1
        for col, value in enumerate(values, start=1):
            cell = _set(ws, row, col, value)
            cell.alignment = WRAP
        if severity_col is not None:
            fill = SEVERITY_FILLS.get(str(values[severity_col - 1]).lower())
            if fill:
                ws.cell(row=row, column=severity_col).fill = PatternFill("solid", fgColor=fill)
    return row + 1


def _set_widths(ws: Worksheet, widths: List[int]) -> None:
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def _pillar_items(report_data: Dict[str, Any]) -> List[tuple]:
    pillars = report_data.get("pillar_results") or {}
    ordered = [name for name in PILLAR_ORDER if name in pillars]
    ordered += [name for name in pillars if name not in ordered]
    return [(name, pillars[name] or {}) for name in ordered]


def _summary_sheet(ws: Worksheet, report: ReportSummary, data: Dict[str, Any], target_url: str) -> None:
    ws.title = "Summary"
    _set(ws, 1, 1, "Website Quality Audit Report").font = TITLE_FONT

    status = "PASSED" if report.passed else "NOT PASSED"
    details = [
        ("Run ID",            report.test_run_id),
        ("Website",           target_url),
        ("Overall score",     f"{report.overall_score:.1f} / 100"),
        ("Grade",             report.grade),
        ("Result",            status),
        ("Total findings",    report.total_findings),
        ("Exported at (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")),
    ]
    if data.get("incomplete"):
        details.append(("Note", data.get("incomplete_reason") or "Partial report: some checks did not finish."))
    row = 3
    for label, value in details:
        _set(ws, row, 1, label).font = Font(bold=True)
        _set(ws, row, 2, value).alignment = WRAP
        row += 1

    row += 1
    _set(ws, row, 1, "Score by check").font = SECTION_FONT
    weights = data.get("effective_pillar_weights") or {}
    pillar_rows = []
    for name, result in _pillar_items(data):
        metrics = result.get("metrics") or {}
        skipped = result.get("status") == "SKIPPED"
        weight  = weights.get(name)
        pillar_rows.append([
            name.capitalize(),
            result.get("status", ""),
            "" if skipped else result.get("score", ""),
            f"{weight * 100:.0f}%" if isinstance(weight, (int, float)) else "",
            metrics.get("finding_count", len(result.get("findings") or [])),
            metrics.get("reason", "") if skipped else metrics.get("score_note", ""),
        ])
    row = _write_table(ws, row + 1, ["Check", "Status", "Score", "Weight in grade", "Findings", "Note"], pillar_rows)

    row += 1
    _set(ws, row, 1, "Findings by severity").font = SECTION_FONT
    counts = data.get("findings_by_severity") or {}
    _write_table(
        ws, row + 1, ["Severity", "Count"],
        [[sev.capitalize(), counts.get(sev, 0)] for sev in SEVERITY_ORDER],
        severity_col=1,
    )
    _set_widths(ws, [22, 60, 12, 16, 10, 60])


def _execution_summary_sheet(ws: Worksheet, data: Dict[str, Any]) -> None:
    ws.title = "Execution Summary"
    func_metrics = ((data.get("pillar_results") or {}).get("functional") or {}).get("metrics") or {}
    cases = data.get("test_cases") or []

    total    = func_metrics.get("total_cases",    len(cases))
    executed = func_metrics.get("executed_cases", len(cases))
    passed   = func_metrics.get("passed_cases",   sum(1 for c in cases if isinstance(c, dict) and c.get("passed") is True))
    failed   = func_metrics.get("failed_cases",   sum(1 for c in cases if isinstance(c, dict) and c.get("passed") is False))
    skipped  = func_metrics.get("skipped_cases",  0)
    coverage = f"{round(executed / total * 100)}%" if total else "0%"

    rows = [
        ["Total Test Cases",          total],
        ["Executed",                  executed],
        ["Passed",                    passed],
        ["Failed",                    failed],
        ["Skipped / Blocked",         skipped],
        ["Functional Coverage",       coverage],
        ["Forms Discovered",          func_metrics.get("forms_discovered", 0)],
        ["Interactive Elements",      func_metrics.get("interactive_elements_discovered", 0)],
        ["AI ReAct Actions",          func_metrics.get("react_actions", 0)],
        ["AI ReAct Completed",        str(func_metrics.get("react_completed", False))],
        ["Pages Attempted",           func_metrics.get("site_pages_attempted", 0)],
        ["Broken Links",              func_metrics.get("broken_links_count", 0)],
        ["JS Errors",                 func_metrics.get("js_errors_count", 0)],
    ]

    _set(ws, 1, 1, "Functional Test Execution Summary").font = TITLE_FONT
    _write_table(ws, 3, ["Metric", "Value"], rows)
    _set_widths(ws, [32, 20])


def _findings_sheet(ws: Worksheet, data: Dict[str, Any]) -> None:
    rows = []
    for name, result in _pillar_items(data):
        for finding in result.get("findings") or []:
            rows.append([
                name.capitalize(),
                str(finding.get("severity", "")).upper(),
                finding.get("id", ""),
                finding.get("title", ""),
                finding.get("description", ""),
                finding.get("selector_or_url", ""),
                finding.get("suggested_fix", ""),
            ])
    rank = {sev.upper(): i for i, sev in enumerate(SEVERITY_ORDER)}
    rows.sort(key=lambda r: (rank.get(r[1], len(rank)), r[0]))
    rows = [[i] + r for i, r in enumerate(rows, start=1)]

    headers = ["#", "Check", "Severity", "Rule", "Title", "Description", "Page / element", "Suggested fix"]
    last = _write_table(ws, 1, headers, rows, severity_col=3)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(last - 1, 1)}"
    _set_widths(ws, [6, 14, 11, 30, 40, 60, 45, 60])


def _metrics_sheet(ws: Worksheet, data: Dict[str, Any]) -> None:
    rows = []
    for name, result in _pillar_items(data):
        for key, value in (result.get("metrics") or {}).items():
            if isinstance(value, (dict, list)) and len(str(value)) > 500:
                continue
            rows.append([name.capitalize(), key.replace("_", " "), value])
    _write_table(ws, 1, ["Check", "Metric", "Value"], rows)
    ws.freeze_panes = "A2"
    _set_widths(ws, [14, 40, 70])


def _test_cases_sheet(ws: Worksheet, data: Dict[str, Any]) -> None:
    ws.title = "Functional Test Cases"
    rows = []
    for i, case in enumerate(data.get("test_cases") or [], start=1):
        if not isinstance(case, dict):
            continue
        passed = case.get("passed")
        status = "PASSED" if passed is True else ("FAILED" if passed is False else case.get("status", "N/A"))
        rows.append([
            f"TC-{i:03d}",
            case.get("module") or (case.get("pillar", "") or "").capitalize() or "General",
            case.get("test_type") or "Functional Testing",
            case.get("test_scenario") or case.get("title", ""),
            len(case.get("steps") or []),
            case.get("expected_result") or "",
            case.get("actual_result") or status,
            status,
            case.get("evidence_path") or "",
        ])

    headers = ["TC ID", "Module", "Test Type", "Test Scenario", "Steps",
               "Expected Result", "Actual Result", "Status", "Evidence"]
    last = _write_table(ws, 1, headers, rows, severity_col=None)

    # Color the Status column (col 8)
    for row_idx in range(2, last):
        cell = ws.cell(row=row_idx, column=8)
        if cell.value == "PASSED":
            cell.fill = PASS_FILL
            cell.font = PASS_FONT
        elif cell.value == "FAILED":
            cell.fill = FAIL_FILL
            cell.font = FAIL_FONT

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(last - 1, 1)}"
    _set_widths(ws, [10, 20, 22, 50, 7, 45, 45, 10, 40])


def build_report_workbook(report: ReportSummary) -> bytes:
    """Return the report as .xlsx bytes with 5 sheets: Summary, Execution Summary, Findings, Metrics, Functional Test Cases."""
    data: Dict[str, Any] = report.report_data or {}
    target_url = data.get("target_url", "")

    wb = Workbook()
    _summary_sheet(wb.active, report, data, target_url)
    _execution_summary_sheet(wb.create_sheet("Execution Summary"), data)
    _findings_sheet(wb.create_sheet("Findings"), data)
    _metrics_sheet(wb.create_sheet("Metrics"), data)
    _test_cases_sheet(wb.create_sheet("Functional Test Cases"), data)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
