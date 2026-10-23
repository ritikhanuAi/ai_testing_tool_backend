from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from app.common.serializers import BaseSchema

class DashboardSummaryResponse(BaseSchema):
    total_websites: int
    total_runs: int
    average_score: float
    runs_by_status: Dict[str, int]
    pass_rate_percentage: float

class RecentActivityItem(BaseSchema):
    run_id: str
    website_id: str
    website_name: str
    target_url: str
    status: str
    score: Optional[float] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
