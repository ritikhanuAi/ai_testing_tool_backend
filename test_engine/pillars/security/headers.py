from typing import Dict, List, Tuple
import re
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
        ),
        "permissions-policy": (
            "Missing Permissions-Policy",
            "Browser features remain available without an explicit site policy.",
            FindingSeverity.LOW,
            "Disable browser capabilities the application does not need with a restrictive Permissions-Policy."
        )
    }

    @classmethod
    def audit_headers(cls, headers: Dict[str, str], url: str) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []
        normalized_headers = {k.lower(): v for k, v in headers.items()}

        deduction = 0.0
        for header_key, (title, desc, severity, fix) in cls.CRITICAL_HEADERS.items():
            if header_key == "strict-transport-security" and not url.lower().startswith("https://"):
                continue
            if header_key == "x-frame-options" and (
                "x-frame-options" in normalized_headers
                or "frame-ancestors" in normalized_headers.get("content-security-policy", "").lower()
            ):
                continue
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

        csp = normalized_headers.get("content-security-policy", "").lower()
        if csp:
            directives = {}
            for part in csp.split(";"):
                tokens = part.strip().split()
                if tokens:
                    directives[tokens[0]] = tokens[1:]
            weak_csp = []
            for directive in ("script-src", "default-src"):
                sources = directives.get(directive, [])
                if "'unsafe-eval'" in sources:
                    weak_csp.append(f"{directive} allows unsafe-eval")
                if "*" in sources:
                    weak_csp.append(f"{directive} allows any origin")
            script_sources = directives.get("script-src", directives.get("default-src", []))
            has_nonce_or_hash = any(
                source.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-"))
                for source in script_sources
            )
            if "'unsafe-inline'" in script_sources and not has_nonce_or_hash:
                weak_csp.append("script policy allows unsafe-inline without a nonce or hash")
            if "object-src" not in directives or "'none'" not in directives.get("object-src", []):
                weak_csp.append("object-src is not restricted to none")
            if weak_csp:
                findings.append(PillarFinding(
                    id="SEC-HDR-CSP-WEAK",
                    title="Content-Security-Policy has weak directives",
                    description="; ".join(weak_csp),
                    severity=FindingSeverity.MEDIUM,
                    selector_or_url=url,
                    suggested_fix="Use a restrictive nonce-based script policy, set object-src 'none', and avoid wildcard sources.",
                ))
                deduction += 5.0

        hsts = normalized_headers.get("strict-transport-security", "").lower()
        if hsts:
            match = re.search(r"max-age\s*=\s*(\d+)", hsts)
            if not match or int(match.group(1)) < 15552000:
                findings.append(PillarFinding(
                    id="SEC-HDR-HSTS-WEAK",
                    title="Strict-Transport-Security policy is weak or malformed",
                    description="The HSTS max-age is missing or shorter than six months.",
                    severity=FindingSeverity.MEDIUM,
                    selector_or_url=url,
                    suggested_fix="Set a long max-age, then add includeSubDomains after confirming every subdomain supports HTTPS.",
                ))
                deduction += 5.0

        server = normalized_headers.get("server", "")
        powered_by = normalized_headers.get("x-powered-by", "")
        if powered_by or re.search(r"\d", server):
            exposed_values = [value for value in (server, powered_by) if value]
            findings.append(PillarFinding(
                id="SEC-HDR-SERVER-TECHNOLOGY-EXPOSED",
                title="Response headers disclose server technology details",
                description=f"The response identifies server software: {', '.join(exposed_values)}.",
                severity=FindingSeverity.LOW,
                selector_or_url=url,
                suggested_fix="Remove version details from Server and X-Powered-By response headers where practical.",
            ))
            deduction += 2.0

        score = max(0.0, 100.0 - deduction)
        return findings, score
