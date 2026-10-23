from typing import List, Dict, Any, Tuple
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class CookieSecurityScanner:
    """Checks cookies for essential HttpOnly, Secure, and SameSite security attributes."""

    @staticmethod
    def audit_cookies(cookies: List[Dict[str, Any]], target_url: str) -> Tuple[List[PillarFinding], float]:
        findings: List[PillarFinding] = []
        deduction = 0.0

        for c in cookies:
            name = c.get("name", "unknown")
            is_secure = c.get("secure", False)
            is_http_only = c.get("httpOnly", False)
            same_site = c.get("sameSite", "").lower()

            if not is_secure and target_url.startswith("https://"):
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-INSECURE-{name}",
                    title=f"Cookie '{name}' lacks 'Secure' flag",
                    description="Cookie can be transmitted over unencrypted HTTP connections.",
                    severity=FindingSeverity.MEDIUM,
                    selector_or_url=name,
                    suggested_fix="Set 'Secure' attribute on sensitive session cookies."
                ))
                deduction += 8.0

            if not is_http_only and any(token in name.lower() for token in ["session", "auth", "token", "jwt"]):
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-NOHTTPONLY-{name}",
                    title=f"Authentication cookie '{name}' lacks 'HttpOnly' flag",
                    description="Cookie can be accessed via client-side JavaScript, increasing XSS session theft risk.",
                    severity=FindingSeverity.HIGH,
                    selector_or_url=name,
                    suggested_fix="Set 'HttpOnly' on all authentication cookies."
                ))
                deduction += 15.0

            if same_site not in ["lax", "strict"]:
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-NOSAMESITE-{name}",
                    title=f"Cookie '{name}' lacks strict 'SameSite' policy",
                    description="Increases risk of Cross-Site Request Forgery (CSRF).",
                    severity=FindingSeverity.LOW,
                    selector_or_url=name,
                    suggested_fix="Set 'SameSite=Lax' or 'SameSite=Strict'."
                ))
                deduction += 4.0

        score = max(0.0, 100.0 - deduction)
        return findings, score
