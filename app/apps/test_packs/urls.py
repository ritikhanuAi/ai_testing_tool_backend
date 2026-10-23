from fastapi import APIRouter, status
from typing import List
from app.common.serializers import ResponseEnvelope
from app.apps.test_packs.serializers import TestPackResponse, TestPackValidationResponse
from app.apps.test_packs.views import list_packs_view, create_pack_view, validate_raw_pack_view

router = APIRouter(prefix="/test-packs", tags=["Test Packs"])

router.get("", response_model=ResponseEnvelope[List[TestPackResponse]], summary="List Test Packs")(list_packs_view)
router.post("", response_model=ResponseEnvelope[TestPackResponse], status_code=status.HTTP_201_CREATED, summary="Create Test Pack")(create_pack_view)
router.post("/validate", response_model=ResponseEnvelope[TestPackValidationResponse], summary="Pre-flight Validate Test Pack YAML")(validate_raw_pack_view)
