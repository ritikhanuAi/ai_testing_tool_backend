from fastapi import APIRouter
from app.apps.dashboard.views import dashboard_summary_view, dashboard_recent_activity_view

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

router.get("/summary", summary="Dashboard KPI & Metrics Summary")(dashboard_summary_view)
router.get("/recent-activity", summary="Recent Test Run Activity Feed")(dashboard_recent_activity_view)
