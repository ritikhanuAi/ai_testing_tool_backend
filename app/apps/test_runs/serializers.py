from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.common.serializers import BaseSchema
from app.common.constants import RunStatus

class TriggerScanRequest(BaseModel):
    website_id: str
    scan_profile_id: Optional[str] = None
    test_pack_id: Optional[str] = None

class TestRunResponse(BaseSchema):
    id: str
    website_id: str
    scan_profile_id: Optional[str] = None
    test_pack_id: Optional[str] = None
    status: RunStatus
    overall_score: Optional[float] = None
    grade: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
