import asyncio
import os
import sys
from pathlib import Path

# Add backend root to sys.path so imports work seamlessly
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

from app.core.database import engine, Base
from app.core.config import settings

# Import all models to ensure they are registered on Base.metadata
import app.apps.websites.models  # noqa
import app.apps.scan_profiles.models  # noqa
import app.apps.test_packs.models  # noqa
import app.apps.test_runs.models  # noqa
import app.apps.reports.models  # noqa

async def clear_database():
    print(f" Connecting to database: {settings.DATABASE_URL.split('@')[-1]}")
    async with engine.begin() as conn:
        print(" Dropping all database tables...")
        await conn.run_sync(Base.metadata.drop_all)
        print(" Recreating clean database tables...")
        await conn.run_sync(Base.metadata.create_all)

    print(" Database schema reset successfully!")

    # Clean local evidence storage (screenshots, traces, logs)
    storage_dir = settings.STORAGE_DIR
    deleted_files = 0
    for sub in ["screenshots", "traces", "logs", "exports"]:
        subpath = os.path.join(storage_dir, sub)
        if os.path.exists(subpath):
            for fname in os.listdir(subpath):
                fpath = os.path.join(subpath, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                        deleted_files += 1
                    except Exception as e:
                        print(f"  Warning: could not delete {fpath}: {e}")
        else:
            os.makedirs(subpath, exist_ok=True)

    print(f" Cleared {deleted_files} artifact files from {storage_dir}/")
    print(" Complete reset finished. Clean slate ready!")

if __name__ == "__main__":
    asyncio.run(clear_database())
