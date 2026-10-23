from sqlalchemy.ext.asyncio import AsyncSession
from app.apps.test_packs.models import TestPack
from app.apps.test_packs.serializers import TestPackCreateRequest
from test_engine.schema.validator import SchemaValidator

async def save_test_pack(db: AsyncSession, data: TestPackCreateRequest) -> TestPack:
    """Validate YAML against Pydantic schema and persist TestPack."""
    is_valid, pack_model, errors = SchemaValidator.validate_yaml_pack(data.raw_yaml)
    cases_count = len(pack_model.cases) if pack_model else 0

    pack = TestPack(
        website_id=data.website_id,
        title=data.title,
        version=data.version,
        raw_yaml=data.raw_yaml,
        cases_count=cases_count,
        is_valid=is_valid,
        validation_errors=errors
    )
    db.add(pack)
    await db.commit()
    await db.refresh(pack)
    return pack
