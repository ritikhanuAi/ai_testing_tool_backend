from pydantic import BaseModel, Field
from typing import Optional
from app.common.serializers import BaseSchema

class ScanProfileCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    check_functional: bool = True
    check_security: bool = True
    check_performance: bool = True
    check_accessibility: bool = True
    allow_state_changing: bool = False
    rate_limit: int = Field(default=10, ge=1, le=50)
    timeout_seconds: int = Field(default=300, ge=30, le=1800)

class ScanProfileResponse(BaseSchema):
    id: str
    name: str
    description: Optional[str] = None
    check_functional: bool
    check_security: bool
    check_performance: bool
    check_accessibility: bool
    allow_state_changing: bool
    rate_limit: int
    timeout_seconds: int
