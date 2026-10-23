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
    async def check_exposures(cls, base_url: str) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []
        deduction = 0.0

        async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
            for path, title, severity in cls.COMMON_EXPOSURE_PATHS:
                target = urljoin(base_url, path)
                try:
                    res = await client.get(target)
                    if res.status_code == 200 and len(res.content) > 0:
                        # Extra validation for .git/HEAD
                        if path == ".git/HEAD" and "ref:" not in res.text:
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
