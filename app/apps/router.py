from fastapi import APIRouter
from app.core.config import settings
from app.apps.accounts.urls import router as accounts_router
from app.apps.websites.urls import router as websites_router
from app.apps.scan_profiles.urls import router as profiles_router
from app.apps.test_packs.urls import router as packs_router
from app.apps.test_runs.urls import router as runs_router
from app.apps.reports.urls import router as reports_router
from app.apps.dashboard.urls import router as dashboard_router
from app.apps.health.urls import router as health_router

# API v1 Router
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(accounts_router)
api_v1_router.include_router(websites_router)
api_v1_router.include_router(profiles_router)
api_v1_router.include_router(packs_router)
api_v1_router.include_router(runs_router)
api_v1_router.include_router(reports_router)

# Root Router
root_router = APIRouter()
root_router.include_router(health_router, prefix="/api")  # /api/health (mirrors RAMS)

@root_router.get("/", tags=["Root"], summary="API Root Service Info")
async def root_service_info():
    """Returns top-level API service discovery information for headless clients."""
    return {
        "service": settings.APP_NAME,
        "version": settings.PROJECT_VERSION,
        "environment": settings.APP_ENV,
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "api_v1": "/api/v1",
        "health_check": "/api/health"
    }

root_router.include_router(api_v1_router)
