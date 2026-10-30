from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.test_runs.serializers import TriggerScanRequest, TestRunResponse, QuickScanRequest, CustomScanRequest
from app.apps.test_runs.services import dispatch_scan_run, dispatch_quick_scan, dispatch_custom_scan
from app.apps.test_runs.selectors import list_test_runs, get_test_run_by_id, list_run_artifacts

async def quick_scan_view(
    data: QuickScanRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TestRunResponse]:
    """Trigger an autonomous end-to-end multi-pillar test scan from URL and options."""
    run = await dispatch_quick_scan(db, data)
    return ResponseEnvelope(
        message="Autonomous AI scan successfully queued",
        data=TestRunResponse.model_validate(run)
    )

async def execute_custom_scan_view(
    data: CustomScanRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[TestRunResponse]:
    """Execute custom structured JSON test suite via Playwright with optional pillar audits and AI diagnosis."""
    run = await dispatch_custom_scan(db, data)
    return ResponseEnvelope(
        message="Custom test run queued successfully",
        data=TestRunResponse.model_validate(run)
    )

async def list_runs_view(
    website_id: Optional[str] = None,
    limit: Optional[int] = 50,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[TestRunResponse]]:
    runs = await list_test_runs(db, website_id, limit=limit or 50)
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

async def get_run_logs_view(
    run_id: str
) -> ResponseEnvelope[dict]:
    """Retrieve real-time console logs, execution stage, and progress percentage for active or completed scan."""
    from app.common.progress import ScanProgressTracker
    progress = await ScanProgressTracker.get_progress(run_id)
    return ResponseEnvelope(
        message="Scan progress and execution console logs retrieved",
        data=progress
    )

async def get_run_video_view(run_id: str):
    """Fetch or stream recorded video for a test run (WebM format)."""
    import os
    from fastapi.responses import FileResponse
    from app.core.config import settings

    video_path = os.path.join(settings.STORAGE_DIR, "videos", f"{run_id}.webm")
    if not os.path.exists(video_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No video recording found for test run {run_id}. Ensure 'record_video: true' was requested."
        )
    return FileResponse(
        video_path,
        media_type="video/webm",
        filename=f"{run_id}.webm",
        headers={"Accept-Ranges": "bytes"}
    )

async def get_run_trace_view(run_id: str):
    """Download Playwright trace zip for a test run (inspectable at trace.playwright.dev)."""
    import os
    from fastapi.responses import FileResponse
    from app.core.config import settings

    trace_path = os.path.join(settings.STORAGE_DIR, "traces", f"{run_id}_trace.zip")
    if not os.path.exists(trace_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No trace recording found for test run {run_id}."
        )
    return FileResponse(
        trace_path,
        media_type="application/zip",
        filename=f"{run_id}_trace.zip"
    )

async def get_run_artifacts_view(
    run_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[dict]]:
    artifacts = await list_run_artifacts(db, run_id)
    items = [{
        "id": a.id,
        "artifact_type": a.artifact_type,
        "relative_path": a.relative_path,
        "file_name": a.file_name,
        "file_size_bytes": a.file_size_bytes,
        "mime_type": a.mime_type,
        "url": f"/storage/{a.relative_path}",
    } for a in artifacts]
    return ResponseEnvelope(
        message="Artifacts retrieved successfully",
        data=items,
        meta={"count": len(items)}
    )
