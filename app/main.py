import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.middleware import setup_middleware
from app.core.exceptions import register_exception_handlers
from app.core.database import engine, Base
from app.apps.router import root_router

# Ensure all models are registered on Base
import app.apps.websites.models  # noqa
import app.apps.scan_profiles.models  # noqa
import app.apps.test_packs.models  # noqa
import app.apps.test_runs.models  # noqa
import app.apps.reports.models  # noqa

# Setup logging immediately
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown event hooks."""
    # Ensure database tables exist (for dev / prototype convenience)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ensure storage evidence directories exist
    for sub in ["screenshots", "traces", "logs", "exports", "videos"]:
        os.makedirs(os.path.join(settings.STORAGE_DIR, sub), exist_ok=True)

    yield

    # Shutdown
    await engine.dispose()

def create_application() -> FastAPI:
    """FastAPI application factory for headless REST backend."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.PROJECT_VERSION,
        description="Deterministic multi-pillar website testing platform backend API.",
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # Configure middleware (CORS, Request ID, Process Time)
    setup_middleware(app)

    # Register unified exception handlers
    register_exception_handlers(app)

    # Mount storage for screenshot and artifact evidence viewing by frontend
    if os.path.exists(settings.STORAGE_DIR):
        app.mount("/storage", StaticFiles(directory=settings.STORAGE_DIR), name="storage")

    # Mount application routers
    app.include_router(root_router)

    return app

app = create_application()
