from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.test_runs.serializers import TriggerScanRequest, TestRunResponse
from app.apps.test_runs.services import dispatch_scan_run
from app.apps.test_runs.selectors import list_test_runs, get_test_run_by_id

async def list_runs_view(
    website_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[TestRunResponse]]:
    runs = await list_test_runs(db, website_id)
    items = [TestRunResponse.model_validate(r) for r in runs]
    return ResponseEnvelope(
        message="Test runs retrieved successfully",
        data=items,
        meta={"count": len(items)}
    )

async def trigger_scan_view(
    data: TriggerScanRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TestRunResponse]:
    run = await dispatch_scan_run(db, data)
    return ResponseEnvelope(
        message="Automated scan dispatched to background queue",
        data=TestRunResponse.model_validate(run)
    )

async def get_run_view(
    run_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TestRunResponse]:
    run = await get_test_run_by_id(db, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Test run not found")
    return ResponseEnvelope(
        message="Test run retrieved successfully",
        data=TestRunResponse.model_validate(run)
    )

async def cancel_run_view(
    run_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TestRunResponse]:
    from app.apps.test_runs.services import cancel_scan_run
    run = await cancel_scan_run(db, run_id)
    return ResponseEnvelope(
        message="Test run cancellation signal sent",
        data=TestRunResponse.model_validate(run)
    )
