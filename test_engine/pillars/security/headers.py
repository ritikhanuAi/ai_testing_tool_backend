from typing import Dict, List, Tuple
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class SecurityHeadersScanner:
    """Audits HTTP response headers for essential web security protections."""

    CRITICAL_HEADERS = {
        "content-security-policy": (
            "Missing Content-Security-Policy (CSP)",
            "CSP prevents Cross-Site Scripting (XSS) and code injection.",
            FindingSeverity.HIGH,
            "Add a Content-Security-Policy header restricting script and resource sources."
        ),
        "strict-transport-security": (
            "Missing Strict-Transport-Security (HSTS)",
            "HSTS enforces HTTPS connections and prevents SSL stripping.",
            FindingSeverity.MEDIUM,
            "Configure 'Strict-Transport-Security: max-age=31536000; includeSubDomains'."
        ),
        "x-frame-options": (
            "Missing X-Frame-Options",
            "X-Frame-Options protects against UI redressing / clickjacking attacks.",
            FindingSeverity.MEDIUM,
            "Set 'X-Frame-Options: DENY' or 'SAMEORIGIN'."
        ),
        "x-content-type-options": (
            "Missing X-Content-Type-Options",
            "Prevents MIME-sniffing vulnerabilities.",
            FindingSeverity.LOW,
            "Set 'X-Content-Type-Options: nosniff'."
        ),
        "referrer-policy": (
            "Missing Referrer-Policy",
            "Prevents leakage of sensitive query parameters in referrer headers.",
            FindingSeverity.LOW,
            "Set 'Referrer-Policy: strict-origin-when-cross-origin'."
        )
    }

    @classmethod
    def audit_headers(cls, headers: Dict[str, str], url: str) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []
        normalized_headers = {k.lower(): v for k, v in headers.items()}

        deduction = 0.0
        for header_key, (title, desc, severity, fix) in cls.CRITICAL_HEADERS.items():
            if header_key not in normalized_headers:
                findings.append(PillarFinding(
                    id=f"SEC-HDR-{header_key.upper().replace('-', '_')}",
                    title=title,
                    description=desc,
                    severity=severity,
                    selector_or_url=url,
                    suggested_fix=fix
                ))
                deduction += 15.0 if severity == FindingSeverity.HIGH else (10.0 if severity == FindingSeverity.MEDIUM else 5.0)

        score = max(0.0, 100.0 - deduction)
        return findings, score
