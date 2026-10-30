from fastapi import APIRouter, status
from typing import List
from app.common.serializers import ResponseEnvelope
from app.apps.test_runs.serializers import TestRunResponse
from app.apps.websites.serializers import WebsiteAnalyzeResponse
from app.apps.websites.views import analyze_website_view
from app.apps.test_runs.views import (
    list_runs_view, trigger_scan_view, get_run_view, cancel_run_view, quick_scan_view,
    get_run_logs_view, execute_custom_scan_view
)

router = APIRouter(prefix="/test-runs", tags=["Test Runs"])

router.get("", response_model=ResponseEnvelope[List[TestRunResponse]], summary="List Test Runs")(list_runs_view)
router.post("", response_model=ResponseEnvelope[TestRunResponse], status_code=status.HTTP_201_CREATED, summary="Trigger Automated Scan")(trigger_scan_view)
router.post("/quick-scan", response_model=ResponseEnvelope[TestRunResponse], status_code=status.HTTP_202_ACCEPTED, summary="Trigger Autonomous AI Quick Scan")(quick_scan_view)
router.post("/execute-custom", response_model=ResponseEnvelope[TestRunResponse], status_code=status.HTTP_202_ACCEPTED, summary="Execute Custom Structured JSON Test Suite")(execute_custom_scan_view)
router.post("/analyze-url", response_model=ResponseEnvelope[WebsiteAnalyzeResponse], summary="Analyze Website URL for Testing Requirements")(analyze_website_view)
router.get("/{run_id}", response_model=ResponseEnvelope[TestRunResponse], summary="Get Test Run By ID")(get_run_view)
router.get("/{run_id}/logs", summary="Get Live Test Run Console Logs & Stage Progress")(get_run_logs_view)
router.post("/{run_id}/cancel", response_model=ResponseEnvelope[TestRunResponse], summary="Cancel Active Test Run")(cancel_run_view)


