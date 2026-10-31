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
    "high": "FDE2CC",
    "medium": "FFF3CD",
    "low": "D1ECF1",
    "info": "E9ECEF",
}
HEADER_FILL = PatternFill("solid", fgColor="212121")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
SECTION_FONT = Font(bold=True, size=12)
WRAP = Alignment(wrap_text=True, vertical="top")


def _clean(value: Any) -> Any:
    """Make a value safe to write into a cell.

    Report text comes from scanned websites, so it is untrusted: remove characters
    Excel cannot store, and turn lists/dicts into readable text.
    """
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
    # Untrusted text starting with "=" must stay text, never become a formula.
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
        ("Run ID", report.test_run_id),
        ("Website", target_url),
        ("Overall score", f"{report.overall_score:.1f} / 100"),
        ("Grade", report.grade),
        ("Result", status),
        ("Total findings", report.total_findings),
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
        weight = weights.get(name)
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
                continue  # skip large nested data that is unreadable in a cell
            rows.append([name.capitalize(), key.replace("_", " "), value])
    _write_table(ws, 1, ["Check", "Metric", "Value"], rows)
    ws.freeze_panes = "A2"
    _set_widths(ws, [14, 40, 70])


def _test_cases_sheet(ws: Worksheet, data: Dict[str, Any]) -> None:
    rows = []
    for case in data.get("test_cases") or []:
        if not isinstance(case, dict):
            continue
        passed = case.get("passed")
        rows.append([
            case.get("id", ""),
            case.get("title", ""),
            "PASSED" if passed is True else ("FAILED" if passed is False else case.get("status", "")),
            len(case.get("steps") or []),
            case.get("assertions_count", ""),
        ])
    _write_table(ws, 1, ["Case ID", "Title", "Result", "Steps", "Assertions"], rows)
    ws.freeze_panes = "A2"
    _set_widths(ws, [24, 60, 12, 8, 12])


def build_report_workbook(report: ReportSummary) -> bytes:
    """Return the report as .xlsx bytes (Summary, Findings, Metrics, Test Cases sheets)."""
    data: Dict[str, Any] = report.report_data or {}
    target_url = data.get("target_url", "")

    wb = Workbook()
    _summary_sheet(wb.active, report, data, target_url)
    _findings_sheet(wb.create_sheet("Findings"), data)
    _metrics_sheet(wb.create_sheet("Metrics"), data)
    if data.get("test_cases"):
        _test_cases_sheet(wb.create_sheet("Test Cases"), data)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
