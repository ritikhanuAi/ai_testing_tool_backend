from pydantic import BaseModel
from typing import Dict, Any, List
from app.common.serializers import BaseSchema

class ReportSummaryResponse(BaseSchema):
    id: str
    test_run_id: str
    overall_score: float
    grade: str
    passed: bool
    functional_score: float
    security_score: float
    performance_score: float
    accessibility_score: float
    total_findings: int
    critical_findings: int
    high_findings: int
    report_data: Dict[str, Any]
