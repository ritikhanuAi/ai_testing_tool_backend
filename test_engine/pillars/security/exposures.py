import json
import re
import asyncio
import time
import httpx
from urllib.parse import urljoin
from typing import List, Tuple
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class SensitiveExposureScanner:
    """Checks for accidental exposure of sensitive files and debug endpoints."""

    COMMON_EXPOSURE_PATHS = [
        (".env", "Exposed .env configuration file", FindingSeverity.CRITICAL),
        (".git/HEAD", "Exposed Git repository metadata (.git/HEAD)", FindingSeverity.HIGH),
        ("phpinfo.php", "Exposed phpinfo diagnostic page", FindingSeverity.HIGH),
        ("actuator/health", "Exposed Spring Boot Actuator endpoint", FindingSeverity.MEDIUM),
    ]

    @classmethod
    async def check_exposures(
        cls,
        base_url: str,
        request_rate_limit: int = 10,
    ) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []
        deduction = 0.0
        interval = 1.0 / max(request_rate_limit, 1)
        last_request = 0.0

        async with httpx.AsyncClient(timeout=4.0, follow_redirects=False, trust_env=False) as client:
            for path, title, severity in cls.COMMON_EXPOSURE_PATHS:
                wait = interval - (time.monotonic() - last_request)
                if last_request and wait > 0:
                    await asyncio.sleep(wait)
                last_request = time.monotonic()
                target = urljoin(base_url, path)
                try:
                    async with client.stream("GET", target) as res:
                        if res.status_code != 200:
                            continue
                        body_parts = []
                        remaining = 8192
                        async for chunk in res.aiter_bytes():
                            body_parts.append(chunk[:remaining])
                            remaining -= min(len(chunk), remaining)
                            if remaining <= 0:
                                break
                        body = b"".join(body_parts)
                        sample = body.decode("utf-8", errors="replace")
                        if path == ".env":
                            looks_like_env = bool(re.search(r"(?m)^\s*[A-Za-z_][A-Za-z0-9_]*\s*=", sample)) and "<html" not in sample.lower()
                            if not looks_like_env:
                                continue
                        elif path == ".git/HEAD":
                            if not re.fullmatch(r"\s*ref:\s*refs/[A-Za-z0-9_./-]+\s*", sample):
                                continue
                        elif path == "phpinfo.php":
                            if "phpinfo()" not in sample.lower() and "php version" not in sample.lower():
                                continue
                        elif path == "actuator/health":
                            try:
                                health = json.loads(sample)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(health, dict) or "status" not in health:
                                continue

                        findings.append(PillarFinding(
                            id=f"SEC-EXPOSURE-{path.replace('.', '').replace('/', '_').upper()}",
                            title=title,
                            description=f"Accessible endpoint found at {target} (HTTP 200).",
                            severity=severity,
                            selector_or_url=target,
                            suggested_fix="Restrict access at web server / reverse proxy level."
                        ))
                        deduction += 30.0 if severity == FindingSeverity.CRITICAL else 15.0
                except Exception:
                    pass

        score = max(0.0, 100.0 - deduction)
        return findings, score
