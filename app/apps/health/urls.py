from fastapi import APIRouter
from app.apps.health.views import health_check_view

router = APIRouter(tags=["Health"])

router.get("/health", summary="System Health & Readiness Probe")(health_check_view)
