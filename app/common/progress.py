import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger("scan_progress")

class ScanProgressTracker:
    """Publishes and retrieves real-time execution logs and progress percentages for test runs."""

    @staticmethod
    async def log(
        run_id: str,
        stage: str,
        message: str,
        progress: int,
        level: str = "INFO"
    ) -> None:
        """
        Emits a log entry both to Celery worker stdout and to Redis for real-time frontend consumption.
        Stages: INITIALIZING, AUTHENTICATING, FUNCTIONAL, SECURITY, PERFORMANCE, ACCESSIBILITY, AGGREGATING, COMPLETED, FAILED.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        entry = {
            "timestamp": now_iso,
            "level": level,
            "stage": stage,
            "message": message,
            "progress": progress
        }

        # 1. Print formatted log to Celery worker console
        log_line = f"[{stage} - {progress}%] {message}"
        if level == "WARNING":
            logger.warning(log_line)
        elif level == "ERROR":
            logger.error(log_line)
        else:
            logger.info(log_line)

        # 2. Persist in Redis with 24-hour expiration
        try:
            r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            pipe = r.pipeline()
            # Push to logs list
            pipe.rpush(f"scan_logs:{run_id}", json.dumps(entry))
            pipe.expire(f"scan_logs:{run_id}", 86400)
            # Update current state hash
            pipe.hset(f"scan_progress:{run_id}", mapping={
                "stage": stage,
                "progress": str(progress),
                "message": message,
                "level": level,
                "updated_at": now_iso
            })
            pipe.expire(f"scan_progress:{run_id}", 86400)
            await pipe.execute()
            await r.aclose()
        except Exception as err:
            logger.debug(f"Could not publish progress to Redis: {err}")

    @staticmethod
    async def get_progress(run_id: str) -> Dict[str, Any]:
        """Fetch current stage, progress percentage, and log history for a test run."""
        try:
            r = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
            progress_data = await r.hgetall(f"scan_progress:{run_id}")
            raw_logs = await r.lrange(f"scan_logs:{run_id}", 0, -1)
            await r.aclose()

            logs: List[Dict[str, Any]] = []
            for item in raw_logs:
                try:
                    text_val = item.decode("utf-8") if isinstance(item, bytes) else item
                    logs.append(json.loads(text_val))
                except Exception:
                    pass

            stage = "QUEUED"
            progress = 0
            message = "Scan queued in worker queue..."
            updated_at = None

            if progress_data:
                decoded = {
                    (k.decode("utf-8") if isinstance(k, bytes) else k):
                    (v.decode("utf-8") if isinstance(v, bytes) else v)
                    for k, v in progress_data.items()
                }
                stage = decoded.get("stage", "RUNNING")
                progress = int(decoded.get("progress", "0"))
                message = decoded.get("message", "")
                updated_at = decoded.get("updated_at")

            return {
                "run_id": run_id,
                "stage": stage,
                "progress": progress,
                "message": message,
                "updated_at": updated_at,
                "logs_count": len(logs),
                "logs": logs
            }
        except Exception as err:
            logger.warning(f"Could not retrieve progress from Redis: {err}")
            return {
                "run_id": run_id,
                "stage": "UNKNOWN",
                "progress": 0,
                "message": f"Could not fetch logs: {err}",
                "logs": []
            }
