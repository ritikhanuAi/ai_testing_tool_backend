from fastapi import APIRouter, Response
from app.common.serializers import ResponseEnvelope
from app.apps.reports.serializers import ReportSummaryResponse
from app.apps.reports.views import get_report_view, export_report_excel_view

router = APIRouter(prefix="/reports", tags=["Reports"])

router.get("/{run_id}", response_model=ResponseEnvelope[ReportSummaryResponse], summary="Get Test Run Report Summary")(get_report_view)
router.get("/{run_id}/export.xlsx", summary="Download Test Run Report as Excel", response_class=Response)(export_report_excel_view)
