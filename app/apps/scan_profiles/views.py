from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.scan_profiles.serializers import ScanProfileCreateRequest, ScanProfileResponse
from app.apps.scan_profiles.selectors import list_scan_profiles, get_scan_profile_by_id
from app.apps.scan_profiles.services import create_scan_profile

async def list_profiles_view(
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[ScanProfileResponse]]:
    profiles = await list_scan_profiles(db)
    items = [ScanProfileResponse.model_validate(p) for p in profiles]
    return ResponseEnvelope(
        message="Scan profiles retrieved successfully",
        data=items,
        meta={"count": len(items)}
    )

async def create_profile_view(
    data: ScanProfileCreateRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[ScanProfileResponse]:
    profile = await create_scan_profile(db, data)
    return ResponseEnvelope(
        message="Scan profile created successfully",
        data=ScanProfileResponse.model_validate(profile)
    )

async def get_profile_view(
    profile_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[ScanProfileResponse]:
    profile = await get_scan_profile_by_id(db, profile_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan profile not found")
    return ResponseEnvelope(
        message="Scan profile details retrieved successfully",
        data=ScanProfileResponse.model_validate(profile)
    )
