"""Database engine for Celery tasks that run in separate asyncio event loops."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings


# Celery's synchronous task entrypoint uses asyncio.run(), which creates and closes
# an event loop for each call. Never keep asyncpg connections in a pool across
# those loops: asyncpg connections belong to the loop that created them.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)
