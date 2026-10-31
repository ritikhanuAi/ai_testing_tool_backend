from fastapi import Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.reports.serializers import ReportSummaryResponse
from app.apps.reports.selectors import get_report_by_run_id
from app.apps.reports.excel_export import build_report_workbook

async def get_report_view(
    run_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[ReportSummaryResponse]:
    report = await get_report_by_run_id(db, run_id)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found for this test run")
    return ResponseEnvelope(
        message="4-pillar audit report retrieved successfully",
        data=ReportSummaryResponse.model_validate(report)
    )


async def export_report_excel_view(
    run_id: str,
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Download the report as an Excel workbook."""
    report = await get_report_by_run_id(db, run_id)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found for this test run")
    content = build_report_workbook(report)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="report-{run_id}.xlsx"'},
    )
