from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import Dict, Any, List
from app.apps.websites.models import Website
from app.apps.test_runs.models import TestRun
from app.apps.reports.models import ReportSummary
from app.common.constants import RunStatus

async def get_dashboard_summary(db: AsyncSession) -> Dict[str, Any]:
    """Retrieve high-level statistics for the dashboard overview."""
    sites_count = await db.scalar(select(func.count(Website.id))) or 0
    runs_count = await db.scalar(select(func.count(TestRun.id))) or 0
    avg_score = await db.scalar(select(func.avg(ReportSummary.overall_score))) or 0.0

    # Count runs by status
    status_counts: Dict[str, int] = {}
    for status_val in [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.FAILED]:
        count = await db.scalar(select(func.count(TestRun.id)).where(TestRun.status == status_val)) or 0
        status_counts[status_val.value] = count

    # Calculate pass rate
    total_completed = status_counts.get("COMPLETED", 0)
    passed_runs = await db.scalar(
        select(func.count(ReportSummary.id)).where(ReportSummary.passed.is_(True))
    ) or 0
    pass_rate = round((passed_runs / total_completed * 100.0), 1) if total_completed > 0 else 0.0

    return {
        "total_websites": sites_count,
        "total_runs": runs_count,
        "average_score": round(float(avg_score), 1),
        "runs_by_status": status_counts,
        "pass_rate_percentage": pass_rate
    }

async def get_recent_activity(db: AsyncSession, limit: int = 10) -> List[Dict[str, Any]]:
    """Retrieve recent test run executions with associated website info and scores."""
    stmt = (
        select(
            TestRun.id.label("run_id"),
            TestRun.status,
            TestRun.started_at,
            TestRun.created_at,
            TestRun.completed_at.label("completed_at"),
            Website.id.label("website_id"),
            Website.name.label("website_name"),
            Website.base_url.label("target_url"),
            func.coalesce(ReportSummary.overall_score, TestRun.overall_score).label("score")
        )
        .outerjoin(Website, TestRun.website_id == Website.id)
        .outerjoin(ReportSummary, TestRun.id == ReportSummary.test_run_id)
        .order_by(desc(func.coalesce(TestRun.started_at, TestRun.created_at)))
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.mappings().all()

    activity = []
    for r in rows:
        activity.append({
            "run_id": r["run_id"],
            "website_id": r["website_id"] or "",
            "website_name": r["website_name"] or "Website Scan",
            "target_url": r["target_url"] or "",
            "status": r["status"].value if hasattr(r["status"], "value") else str(r["status"]),
            "score": float(r["score"]) if r["score"] is not None else None,
            "started_at": r["started_at"] or r["created_at"],
            "completed_at": r["completed_at"],
            "finished_at": r["completed_at"]
        })
    return activity
