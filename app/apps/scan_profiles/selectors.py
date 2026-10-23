from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.apps.scan_profiles.models import ScanProfile

async def list_scan_profiles(db: AsyncSession) -> List[ScanProfile]:
    stmt = select(ScanProfile)
    result = await db.execute(stmt)
    return list(result.scalars().all())

async def get_scan_profile_by_id(db: AsyncSession, profile_id: str) -> Optional[ScanProfile]:
    stmt = select(ScanProfile).where(ScanProfile.id == profile_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
