from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.test_packs.serializers import (
    TestPackCreateRequest, TestPackResponse, TestPackValidationResponse
)
from app.apps.test_packs.services import save_test_pack
from app.apps.test_packs.selectors import list_test_packs, get_test_pack_by_id
from test_engine.schema.validator import SchemaValidator

async def list_packs_view(
    website_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[TestPackResponse]]:
    packs = await list_test_packs(db, website_id)
    items = [TestPackResponse.model_validate(p) for p in packs]
    return ResponseEnvelope(
        message="Test packs retrieved successfully",
        data=items,
        meta={"count": len(items)}
    )

async def create_pack_view(
    data: TestPackCreateRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TestPackResponse]:
    pack = await save_test_pack(db, data)
    return ResponseEnvelope(
        message="Test pack saved successfully",
        data=TestPackResponse.model_validate(pack)
    )

async def validate_raw_pack_view(
    raw_yaml: str
) -> ResponseEnvelope[TestPackValidationResponse]:
    """Pre-flight check without saving to DB."""
    is_valid, pack_model, errors = SchemaValidator.validate_yaml_pack(raw_yaml)
    result = TestPackValidationResponse(
        is_valid=is_valid,
        cases_count=len(pack_model.cases) if pack_model else 0,
        errors=errors
    )
    return ResponseEnvelope(
        message="Test pack pre-flight validation complete",
        data=result
    )
