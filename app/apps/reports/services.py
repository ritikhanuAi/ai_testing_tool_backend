from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any
from app.apps.reports.models import ReportSummary

async def save_report(db: AsyncSession, test_run_id: str, report_data: Dict[str, Any]) -> ReportSummary:
    """Save finalized 4-pillar report summary and findings."""
    pillar_res = report_data.get("pillar_results", {})
    sev_counts = report_data.get("findings_by_severity", {})

    func_score = pillar_res.get("functional", {}).get("score", 100.0)
    sec_score = pillar_res.get("security", {}).get("score", 100.0)
    perf_score = pillar_res.get("performance", {}).get("score", 100.0)
    a11y_score = pillar_res.get("accessibility", {}).get("score", 100.0)

    report = ReportSummary(
        test_run_id=test_run_id,
        overall_score=report_data.get("overall_score", 0.0),
        grade=report_data.get("grade", "F"),
        passed=report_data.get("passed", False),
        functional_score=func_score,
        security_score=sec_score,
        performance_score=perf_score,
        accessibility_score=a11y_score,
        total_findings=report_data.get("total_findings", 0),
        critical_findings=sev_counts.get("critical", 0),
        high_findings=sev_counts.get("high", 0),
        report_data=report_data
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report
