from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from app.apps.reports.models import ReportSummary

async def get_report_by_run_id(db: AsyncSession, run_id: str) -> Optional[ReportSummary]:
    stmt = select(ReportSummary).where(ReportSummary.test_run_id == run_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
