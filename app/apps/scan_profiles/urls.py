from fastapi import APIRouter, status
from typing import List
from app.common.serializers import ResponseEnvelope
from app.apps.scan_profiles.serializers import ScanProfileResponse
from app.apps.scan_profiles.views import list_profiles_view, create_profile_view, get_profile_view

router = APIRouter(prefix="/scan-profiles", tags=["Scan Profiles"])

router.get("", response_model=ResponseEnvelope[List[ScanProfileResponse]], summary="List Scan Profiles")(list_profiles_view)
router.post("", response_model=ResponseEnvelope[ScanProfileResponse], status_code=status.HTTP_201_CREATED, summary="Create Scan Profile")(create_profile_view)
router.get("/{profile_id}", response_model=ResponseEnvelope[ScanProfileResponse], summary="Get Scan Profile By ID")(get_profile_view)
