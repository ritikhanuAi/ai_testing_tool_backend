import pytest
from test_engine.pillars.security.headers import SecurityHeadersScanner
from test_engine.pillars.security.cookies import CookieSecurityScanner
from test_engine.scoring.rubrics import calculate_overall_score, assign_grade
from app.common.constants import PillarType

def test_security_headers_scanner():
    mock_headers = {
        "content-type": "text/html",
        "content-security-policy": "default-src 'self'",
        "strict-transport-security": "max-age=31536000",
        "x-frame-options": "DENY"
    }
    findings, score = SecurityHeadersScanner.audit_headers(mock_headers, "https://example.com")
    assert score >= 80.0
    # Missing x-content-type-options and referrer-policy
    assert len(findings) == 2

def test_cookie_security_scanner():
    mock_cookies = [
        {"name": "session_id", "secure": True, "httpOnly": True, "sameSite": "Lax"},
        {"name": "theme", "secure": True, "httpOnly": False, "sameSite": "None"}
    ]
    findings, score = CookieSecurityScanner.audit_cookies(mock_cookies, "https://example.com")
    assert score > 90.0

def test_scoring_rubric():
    scores = {
        PillarType.FUNCTIONAL: 100.0,
        PillarType.SECURITY: 90.0,
        PillarType.PERFORMANCE: 80.0,
        PillarType.ACCESSIBILITY: 85.0
    }
    overall = calculate_overall_score(scores)
    grade = assign_grade(overall)
    # (100 * 0.35) + (90 * 0.25) + (80 * 0.20) + (85 * 0.20) = 35 + 22.5 + 16 + 17 = 90.5
    assert overall == 90.5
    assert grade == "A"

def test_tls_scanner_plain_http():
    from test_engine.pillars.security.tls import TLSScanner
    findings, score, metrics = TLSScanner.audit_tls("http://example.com")
    assert score == 50.0
    assert len(findings) == 1
    assert findings[0].id == "SEC-NO-TLS"
    assert metrics["tls_enabled"] is False

def test_pillar_finding_fingerprint():
    from test_engine.pillars.base import PillarFinding
    from app.common.constants import FindingSeverity
    finding1 = PillarFinding(
        id="SEC-NO-TLS",
        title="Target site does not use HTTPS",
        description="Plain HTTP",
        severity=FindingSeverity.HIGH,
        selector_or_url="http://example.com"
    )
    finding2 = PillarFinding(
        id="SEC-NO-TLS",
        title="Target site does not use HTTPS",
        description="Different description text",
        severity=FindingSeverity.HIGH,
        selector_or_url="http://example.com"
    )
    # Fingerprint is deterministic based on id, url, and title
    assert finding1.fingerprint == finding2.fingerprint
    assert len(finding1.fingerprint) == 16

def test_calculate_pillar_deductions_capping():
    from test_engine.pillars.base import PillarFinding
    from test_engine.scoring.rubrics import calculate_pillar_deductions
    from app.common.constants import FindingSeverity
    
    # 5 findings with the same rule ID
    findings = [
        PillarFinding(id="SEC-HEADER-CSP", title="Missing CSP", description="", severity=FindingSeverity.HIGH)
        for _ in range(5)
    ]
    # High = -8 pts, capped at 3 occurrences = 3 * 8 = 24 pts deduction
    score, deductions = calculate_pillar_deductions(findings, max_occurrences_per_rule=3)
    assert len(deductions) == 3
    assert score == 100.0 - 24.0  # 76.0
