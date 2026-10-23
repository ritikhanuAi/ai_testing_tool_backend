from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.apps.test_runs.models import TestRun

async def list_test_runs(db: AsyncSession, website_id: Optional[str] = None, limit: int = 50) -> List[TestRun]:
    stmt = select(TestRun).order_by(TestRun.created_at.desc()).limit(limit)
    if website_id:
        stmt = stmt.where(TestRun.website_id == website_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_test_run_by_id(db: AsyncSession, run_id: str) -> Optional[TestRun]:
    stmt = select(TestRun).where(TestRun.id == run_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
