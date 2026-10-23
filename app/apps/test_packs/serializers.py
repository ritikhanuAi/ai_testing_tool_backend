from pydantic import BaseModel, Field
from typing import List, Optional
from app.common.serializers import BaseSchema

class TestPackCreateRequest(BaseModel):
    website_id: Optional[str] = None
    title: str = Field(..., max_length=255)
    version: str = Field(default="1.0.0", max_length=50)
    raw_yaml: str = Field(..., description="YAML formatted test pack string")

class TestPackResponse(BaseSchema):
    id: str
    website_id: Optional[str] = None
    title: str
    version: str
    raw_yaml: str
    cases_count: int
    is_valid: bool
    validation_errors: List[str]

class TestPackValidationResponse(BaseModel):
    is_valid: bool
    cases_count: int
    errors: List[str]
