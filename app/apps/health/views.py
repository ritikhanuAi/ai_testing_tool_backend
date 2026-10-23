from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
import httpx
import redis.asyncio as aioredis
from app.core.database import AsyncSessionLocal
from app.core.config import settings

async def health_check_view():
    """Unauthenticated liveness & readiness health probe mirroring RAMS /api/health."""
    db_ok = False
    redis_ok = False
    ai_engine_ok = False

    # 1. Probe PostgreSQL Database
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False

    # 2. Probe Redis
    try:
        r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        redis_ok = True
    except Exception:
        redis_ok = False

    # 3. Probe Ollama AI Engine (optional readiness)
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            ai_engine_ok = resp.status_code == 200
    except Exception:
        ai_engine_ok = False

    is_healthy = db_ok and redis_ok
    status_code = status.HTTP_200_OK if is_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if is_healthy else "degraded",
            "version": settings.PROJECT_VERSION,
            "components": {
                "database": db_ok,
                "redis": redis_ok,
                "ai_engine": ai_engine_ok
            }
        }
    )
