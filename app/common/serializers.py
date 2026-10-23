from pydantic import BaseModel, ConfigDict, Field
from typing import Generic, TypeVar, Optional, List, Any, Dict

T = TypeVar("T")

class BaseSchema(BaseModel):
    """Base Pydantic schema with standard serialization config."""
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        arbitrary_types_allowed=True
    )

class ResponseEnvelope(BaseSchema, Generic[T]):
    """Standard API response envelope mirroring RAMS SuccessResponse."""
    success: bool = True
    message: str = "OK"
    data: Optional[T] = None
    meta: Optional[Dict[str, Any]] = None

class ErrorDetail(BaseSchema):
    code: str
    message: str
    details: Optional[Any] = None

class ErrorEnvelope(BaseSchema):
    """Standard API error response wrapper."""
    success: bool = False
    error: ErrorDetail
