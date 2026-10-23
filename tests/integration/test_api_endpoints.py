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
    response = client.post("/api/v1/accounts/register", json={})
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
