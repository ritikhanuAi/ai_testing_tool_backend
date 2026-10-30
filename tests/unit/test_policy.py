import pytest
from test_engine.policy.allowlist import TargetAllowlistValidator
from test_engine.policy.risk_checker import RiskPolicyChecker
from test_engine.schema.case import TestCaseModel
from app.common.constants import RiskLevel, PillarType

def test_allowlist_exact_match():
    allowed = ["example.com", "staging.example.com"]
    valid, msg = TargetAllowlistValidator.validate_target("https://example.com/login", allowed)
    assert valid is True

def test_allowlist_wildcard_match(monkeypatch):
    monkeypatch.setattr(
        TargetAllowlistValidator, "assert_public_host", lambda host: (True, "Host is a public address")
    )
    allowed = ["*.mycompany.org"]
    valid, msg = TargetAllowlistValidator.validate_target("https://app.mycompany.org", allowed)
    assert valid is True

def test_allowlist_unauthorized_rejection():
    allowed = ["example.com"]
    valid, msg = TargetAllowlistValidator.validate_target("https://malicious-site.com", allowed)
    assert valid is False
    assert "not in the approved allowlist" in msg

def test_risk_checker_read_only_allowed():
    case = TestCaseModel(
        id="C1", title="Test", pillar=PillarType.FUNCTIONAL,
        steps=[], assertions=[], risk=RiskLevel.READ_ONLY
    )
    permitted, _ = RiskPolicyChecker.is_execution_permitted(case, allow_state_changing=False)
    assert permitted is True

def test_risk_checker_state_changing_blocked_on_read_only_profile():
    case = TestCaseModel(
        id="C2", title="Write Test", pillar=PillarType.FUNCTIONAL,
        steps=[], assertions=[], risk=RiskLevel.STATE_CHANGING
    )
    permitted, reason = RiskPolicyChecker.is_execution_permitted(case, allow_state_changing=False)
    assert permitted is False
    assert "requires 'state_changing' write permission" in reason

def test_ssrf_assert_public_host_blocks_private_and_localhost():
    is_public, msg = TargetAllowlistValidator.assert_public_host("localhost")
    assert is_public is False
    assert "prohibited" in msg.lower()

    is_public, msg = TargetAllowlistValidator.assert_public_host("127.0.0.1")
    assert is_public is False
    assert "protected/internal" in msg.lower()

    is_public, msg = TargetAllowlistValidator.assert_public_host("169.254.169.254")
    assert is_public is False
    assert "blocked network" in msg.lower() or "protected/internal" in msg.lower()

def test_ssrf_assert_public_host_allows_public_ip():
    is_public, _ = TargetAllowlistValidator.assert_public_host("8.8.8.8")
    assert is_public is True
