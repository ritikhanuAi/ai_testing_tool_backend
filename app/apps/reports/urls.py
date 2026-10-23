from fastapi import APIRouter
from app.common.serializers import ResponseEnvelope
from app.apps.reports.serializers import ReportSummaryResponse
from app.apps.reports.views import get_report_view

router = APIRouter(prefix="/reports", tags=["Reports"])

router.get("/{run_id}", response_model=ResponseEnvelope[ReportSummaryResponse], summary="Get Test Run Report Summary")(get_report_view)
