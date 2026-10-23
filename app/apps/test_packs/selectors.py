from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.apps.test_packs.models import TestPack

async def list_test_packs(db: AsyncSession, website_id: Optional[str] = None) -> List[TestPack]:
    stmt = select(TestPack)
    if website_id:
        stmt = stmt.where(TestPack.website_id == website_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_test_pack_by_id(db: AsyncSession, pack_id: str) -> Optional[TestPack]:
    stmt = select(TestPack).where(TestPack.id == pack_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
