import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_root_service_info():
    """Verify headless API root returns service discovery info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "version" in data
    assert data["docs_url"] == "/docs"
    assert data["health_check"] == "/api/health"

def test_health_endpoint():
    """Verify health check probe returns component status."""
    response = client.get("/api/health")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "components" in data
    assert "database" in data["components"]
    assert "redis" in data["components"]

def test_dashboard_summary_envelope():
    """Verify dashboard summary returns standardized ResponseEnvelope."""
    mock_summary = {
        "total_websites": 5,
        "total_runs": 12,
        "average_score": 88.5,
        "runs_by_status": {"QUEUED": 1, "RUNNING": 1, "COMPLETED": 9, "FAILED": 1},
        "pass_rate_percentage": 88.9
    }
    with patch("app.apps.dashboard.views.get_dashboard_summary", new=AsyncMock(return_value=mock_summary)):
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["message"] == "Dashboard summary retrieved successfully"
        assert "data" in payload
        assert payload["data"]["total_websites"] == 5
        assert payload["data"]["average_score"] == 88.5

def test_cors_preflight_for_nextjs():
    """Verify CORS headers permit Next.js on localhost:3000."""
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,Authorization"
    }
    response = client.options("/api/v1/websites", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") == "true"

def test_validation_error_envelope():
    """Verify validation errors are wrapped in unified error envelope."""
    response = client.post("/api/v1/websites", json={})
    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "details" in payload["error"]

def test_not_found_error_envelope():
    """Verify 404 errors are formatted into unified error envelope."""
    with patch("app.apps.websites.views.get_website_by_id", new=AsyncMock(return_value=None)):
        response = client.get("/api/v1/websites/non-existent-uuid")
        assert response.status_code == 404
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == "HTTP_404"
        assert "not found" in payload["error"]["message"].lower()

def test_quick_scan_dispatch_endpoint():
    """Verify quick scan endpoint accepts URL, credentials, and advanced options."""
    payload = {
        "url": "https://example.com",
        "name": "Example Test Site",
        "credentials": {
            "enabled": True,
            "login_url": "https://example.com/login",
            "username": "tester@example.com",
            "password": "SecretPassword123"
        },
        "options": {
            "check_functional": True,
            "check_security": True,
            "check_performance": True,
            "check_accessibility": True,
            "browser_type": "chromium",
            "use_ai_generation": True,
            "ai_test_goals": "Verify home page layout"
        }
    }
    mock_run = {
        "id": "run-test-uuid-1234",
        "website_id": "site-test-uuid-5678",
        "scan_profile_id": "profile-test-uuid-9999",
        "test_pack_id": None,
        "status": "queued",
        "celery_task_id": "celery-mock-task-id",
        "overall_score": None,
        "grade": None,
        "started_at": "2026-10-23T12:00:00Z",
        "completed_at": None,
        "error_message": None
    }
    with patch("app.apps.test_runs.views.dispatch_quick_scan", new=AsyncMock(return_value=mock_run)):
        response = client.post("/api/v1/test-runs/quick-scan", json=payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        assert body["data"]["id"] == "run-test-uuid-1234"
        assert body["data"]["status"] == "queued"

def test_analyze_website_public_url():
    """Verify URL analysis accurately identifies public websites with no credentials required."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com"
    mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
    mock_resp.text = "<html><head><title>Example Domain</title></head><body><h1>Welcome</h1><p>Public info</p></body></html>"

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        response = client.post("/api/v1/websites/analyze", json={"url": "https://example.com"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        data = payload["data"]
        assert data["accessible"] is True
        assert data["is_public_website"] is True
        assert data["needs_credentials"] is False
        assert data["page_title"] == "Example Domain"
        assert data["credentials_requirement"]["required"] is False
        assert data["recommended_options"]["allow_state_changing"] is False
        assert data["recommended_options"]["check_functional"] is True
        assert "ready_quick_scan_payload" in data
        assert data["ready_quick_scan_payload"]["credentials"]["enabled"] is False

def test_analyze_website_auth_gated_url():
    """Verify URL analysis detects login form and recommends credentials."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.url = "https://example.com/login"
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = "<html><head><title>Sign In</title></head><body><form action='/login'><input name='email'><input type='password' name='password'></form></body></html>"

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        response = client.post("/api/v1/test-runs/analyze-url", json={"url": "https://example.com"})
        assert response.status_code == 200
        payload = response.json()
        data = payload["data"]
        assert data["needs_credentials"] is True
        assert data["credentials_requirement"]["required"] is True
        assert data["credentials_requirement"]["auth_method"] == "form"
        assert "password" in data["credentials_requirement"]["detected_fields"]
        assert data["ready_quick_scan_payload"]["credentials"]["enabled"] is True

def test_analyze_website_ssrf_blocked():
    """Verify SSRF protection blocks local / private IP probe requests."""
    response = client.post("/api/v1/websites/analyze", json={"url": "http://127.0.0.1:8000"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "SCOPE_VIOLATION"
    assert "target scope" in payload["error"]["message"].lower() or "protected" in payload["error"]["message"].lower()

def test_get_run_logs_endpoint():
    """Verify GET /api/v1/test-runs/{run_id}/logs returns envelope with progress structure."""
    response = client.get("/api/v1/test-runs/test-run-123/logs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    assert data["run_id"] == "test-run-123"
    assert "stage" in data
    assert "progress" in data
    assert "logs" in data
    assert isinstance(data["logs"], list)

def test_execute_custom_scan_with_array():
    """Verify POST /api/v1/test-runs/execute-custom accepts direct cases array."""
    custom_payload = {
        "url": "https://example.com",
        "name": "Custom Test Suite Array",
        "options": {
            "check_functional": True,
            "check_security": False
        },
        "cases": [
            {
                "id": "TC-001",
                "title": "Home navigation",
                "steps": [
                    {"action": "open_page", "value": "https://example.com"},
                    {"action": "wait", "timeout_ms": 500}
                ],
                "assertions": [
                    {"type": "url_contains", "value": "example.com"}
                ]
            }
        ]
    }
    mock_run = {
        "id": "run-custom-uuid-1",
        "website_id": "site-test-uuid-1",
        "scan_profile_id": "profile-test-uuid-1",
        "test_pack_id": "pack-test-uuid-1",
        "status": "queued",
        "celery_task_id": "celery-mock-task-1",
        "overall_score": None,
        "grade": None,
        "started_at": "2026-10-23T12:00:00Z",
        "completed_at": None,
        "error_message": None
    }
    with patch("app.apps.test_runs.views.dispatch_custom_scan", new=AsyncMock(return_value=mock_run)):
        response = client.post("/api/v1/test-runs/execute-custom", json=custom_payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "queued"
        assert body["data"]["id"] == "run-custom-uuid-1"

def test_execute_custom_scan_with_nested_dict():
    """Verify POST /api/v1/test-runs/execute-custom accepts cases as {'cases': [...]}."""
    custom_payload = {
        "url": "https://example.com",
        "name": "Custom Test Suite Dict",
        "cases": {
            "cases": [
                {
                    "id": "TC-002",
                    "title": "Search test",
                    "steps": [
                        {"action": "open_page", "value": "https://example.com"}
                    ],
                    "assertions": [
                        {"type": "page_title_contains", "value": "Example"}
                    ]
                }
            ]
        }
    }
    mock_run = {
        "id": "run-custom-uuid-2",
        "website_id": "site-test-uuid-2",
        "scan_profile_id": "profile-test-uuid-2",
        "test_pack_id": "pack-test-uuid-2",
        "status": "queued",
        "celery_task_id": "celery-mock-task-2",
        "overall_score": None,
        "grade": None,
        "started_at": "2026-10-23T12:00:00Z",
        "completed_at": None,
        "error_message": None
    }
    with patch("app.apps.test_runs.views.dispatch_custom_scan", new=AsyncMock(return_value=mock_run)):
        response = client.post("/api/v1/test-runs/execute-custom", json=custom_payload)
        assert response.status_code == 202
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "queued"

def test_custom_scan_request_schema_validation():
    """Verify CustomScanRequest Pydantic validator handles array, dict, and stringified JSON."""
    from app.apps.test_runs.serializers import CustomScanRequest

    # 1. Direct List
    req1 = CustomScanRequest(
        url="https://example.com",
        cases=[
            {
                "id": "TC-01",
                "title": "Test 1",
                "steps": [{"action": "open_page", "value": "https://example.com"}],
                "assertions": []
            }
        ]
    )
    assert len(req1.cases) == 1
    assert req1.cases[0].id == "TC-01"

    # 2. Dict with 'cases' key: {"cases": [...]}
    req2 = CustomScanRequest(
        url="https://example.com",
        cases={
            "cases": [
                {
                    "id": "TC-02",
                    "title": "Test 2",
                    "steps": [{"action": "open_page", "value": "https://example.com"}],
                    "assertions": []
                }
            ]
        }
    )
    assert len(req2.cases) == 1
    assert req2.cases[0].id == "TC-02"

    # 3. Stringified JSON from file upload
    json_str = '{"cases": [{"id": "TC-03", "title": "Test 3", "steps": [{"action": "open_page", "value": "https://example.com"}], "assertions": []}]}'
    req3 = CustomScanRequest(
        url="https://example.com",
        cases=json_str
    )
    assert len(req3.cases) == 1
    assert req3.cases[0].id == "TC-03"

def test_run_video_endpoint_not_found():
    """Verify 404 is returned when video file does not exist."""
    response = client.get("/api/v1/test-runs/nonexistent-run-id/video")
    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "HTTP_404"

def test_run_trace_endpoint_not_found():
    """Verify 404 is returned when trace file does not exist."""
    response = client.get("/api/v1/test-runs/nonexistent-run-id/trace")
    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "HTTP_404"

def test_run_artifacts_endpoint_empty():
    """Verify artifacts list endpoint returns empty envelope when run has no artifacts."""
    with patch("app.apps.test_runs.views.list_run_artifacts", new=AsyncMock(return_value=[])):
        response = client.get("/api/v1/test-runs/nonexistent-run-id/artifacts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"] == []


