from sqlalchemy.ext.asyncio import AsyncSession
from app.apps.scan_profiles.models import ScanProfile
from app.apps.scan_profiles.serializers import ScanProfileCreateRequest

async def create_scan_profile(db: AsyncSession, data: ScanProfileCreateRequest) -> ScanProfile:
    profile = ScanProfile(**data.model_dump())
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile
