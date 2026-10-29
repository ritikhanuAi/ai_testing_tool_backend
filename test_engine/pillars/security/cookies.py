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

            if same_site not in ["lax", "strict", "none"]:
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-NOSAMESITE-{name}",
                    title=f"Cookie '{name}' lacks strict 'SameSite' policy",
                    description="Increases risk of Cross-Site Request Forgery (CSRF).",
                    severity=FindingSeverity.LOW,
                    selector_or_url=name,
                    suggested_fix="Set 'SameSite=Lax' or 'SameSite=Strict'."
                ))
                deduction += 4.0
            elif same_site == "none" and not is_secure:
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-NONE-NOT-SECURE-{name}",
                    title=f"Cookie '{name}' uses SameSite=None without Secure",
                    description="Modern browsers require Secure for cross-site cookies, and the cookie may be exposed over HTTP.",
                    severity=FindingSeverity.HIGH,
                    selector_or_url=name,
                    suggested_fix="Set both Secure and SameSite=None only when cross-site use is required.",
                ))
                deduction += 15.0

            if name.startswith("__Host-") and (not is_secure or c.get("path") != "/"):
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-HOST-PREFIX-{name}",
                    title=f"Cookie '{name}' violates the __Host- prefix rules",
                    description="A __Host- cookie must be Secure and use Path=/. The browser's cookie snapshot does not expose whether Domain was omitted.",
                    severity=FindingSeverity.HIGH,
                    selector_or_url=name,
                    suggested_fix="Set Secure and Path=/; confirm the Set-Cookie header omits Domain.",
                ))
                deduction += 15.0

            if name.startswith("__Secure-") and not is_secure:
                findings.append(PillarFinding(
                    id=f"SEC-COOKIE-SECURE-PREFIX-{name}",
                    title=f"Cookie '{name}' violates the __Secure- prefix rules",
                    description="A __Secure- cookie must be marked Secure.",
                    severity=FindingSeverity.HIGH,
                    selector_or_url=name,
                    suggested_fix="Set the Secure attribute on this cookie.",
                ))
                deduction += 15.0

        score = max(0.0, 100.0 - deduction)
        return findings, score
