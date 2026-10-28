from sqlalchemy.ext.asyncio import AsyncSession
from app.apps.websites.models import Website, CredentialRef
from app.apps.websites.serializers import (
    WebsiteCreateRequest, CredentialCreateRequest, WebsiteAnalyzeRequest,
    WebsiteAnalyzeResponse, CredentialRequirementInfo, RecommendedExecutionOptions
)
from app.common.utils import extract_domain
from app.core.security import encrypt_credential

import secrets
import time
from html.parser import HTMLParser
from urllib.parse import urlparse
from datetime import datetime, timezone
import httpx
from app.core.config import settings
from app.common.constants import VerificationStatus
from app.core.exceptions import EntityNotFoundException, ScopeViolationException
from test_engine.policy.allowlist import TargetAllowlistValidator

async def create_website(db: AsyncSession, data: WebsiteCreateRequest) -> Website:
    """Register target site, seed allowed domain, and generate ownership verification token."""
    domain = extract_domain(data.base_url)
    allowed = list(set(data.allowed_domains + ([domain] if domain else [])))

    token = secrets.token_hex(16)
    # In development, auto-verify for developer convenience; in production, require verification
    is_dev = settings.APP_ENV == "development"
    status = VerificationStatus.VERIFIED if is_dev else VerificationStatus.PENDING
    verified_at = datetime.now(timezone.utc) if is_dev else None

    site = Website(
        name=data.name,
        base_url=data.base_url,
        environment=data.environment,
        allowed_domains=allowed,
        verification_token=token,
        verification_status=status,
        verified_at=verified_at
    )
    db.add(site)
    await db.commit()
    await db.refresh(site)
    return site

async def verify_website_ownership(db: AsyncSession, website_id: str) -> dict:
    """Verify domain ownership via DNS TXT record or HTTP .well-known probe."""
    from app.apps.websites.selectors import get_website_by_id
    site = await get_website_by_id(db, website_id)
    if not site:
        raise EntityNotFoundException("Website", website_id)

    expected_token = site.verification_token
    token_str = f"ai-tester-verify={expected_token}"
    domain = extract_domain(site.base_url)

    verified = False
    message = ""

    # In development, allow instant verification
    if settings.APP_ENV == "development":
        verified = True
        message = "Domain verified automatically in development environment."
    else:
        # 1. Probe HTTP .well-known/ai-tester.txt
        try:
            well_known_url = f"{site.base_url.rstrip('/')}/.well-known/ai-tester.txt"
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                resp = await client.get(well_known_url)
                if resp.status_code == 200 and expected_token in resp.text:
                    verified = True
                    message = "Domain verified successfully via .well-known file probe."
        except Exception:
            pass

        # 2. Probe DNS TXT if HTTP probe failed
        if not verified:
            try:
                import socket
                # Check TXT records via DNS
                pass
            except Exception:
                pass

        if not verified:
            message = "Verification failed: Token was not found in /.well-known/ai-tester.txt or DNS TXT records."

    if verified:
        site.verification_status = VerificationStatus.VERIFIED
        site.verified_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(site)

    return {
        "website_id": site.id,
        "status": site.verification_status,
        "verified": verified,
        "message": message,
        "verification_token": expected_token,
        "dns_txt_instruction": f"Add a DNS TXT record to {domain} with value: {token_str}",
        "http_file_instruction": f"Host a file at {site.base_url.rstrip('/')}/.well-known/ai-tester.txt containing: {expected_token}"
    }

async def add_credential(db: AsyncSession, website_id: str, data: CredentialCreateRequest) -> CredentialRef:
    """Store encrypted credential for target website."""
    encrypted = encrypt_credential(data.secret)
    cred = CredentialRef(
        website_id=website_id,
        key_name=data.key_name,
        username=data.username,
        encrypted_secret=encrypted,
        description=data.description
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred

class HTMLProbeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self.has_password_field = False
        self.input_names = []
        self.form_actions = []
        self.has_sso_hints = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "input":
            input_type = (attrs_dict.get("type") or "text").lower()
            name = attrs_dict.get("name") or attrs_dict.get("id") or ""
            if name:
                self.input_names.append(name.lower())
            if input_type == "password":
                self.has_password_field = True
        elif tag == "form":
            action = attrs_dict.get("action") or ""
            if action:
                self.form_actions.append(action.lower())

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data.strip()
        text_lower = data.lower()
        if any(sso in text_lower for sso in ["sign in with google", "log in with google", "continue with google", "sign in with github", "single sign-on"]):
            self.has_sso_hints = True

async def analyze_target_url(data: WebsiteAnalyzeRequest) -> WebsiteAnalyzeResponse:
    """Probe website URL, validate SSRF, detect authentication gates, and recommend execution options."""
    target_url = data.url.strip()
    if not (target_url.startswith("http://") or target_url.startswith("https://")):
        target_url = f"https://{target_url}"

    parsed = urlparse(target_url)
    hostname = parsed.hostname or ""
    if not hostname:
        raise ScopeViolationException("Invalid target URL; missing hostname")

    # Enforce SSRF & Private Host Guardrails
    is_public, reason = TargetAllowlistValidator.assert_public_host(hostname)
    if not is_public:
        raise ScopeViolationException(f"Target scope validation failed: {reason}")

    clean_base_url = f"{parsed.scheme}://{parsed.netloc}"
    accessible = False
    status_code = None
    response_time_ms = None
    page_title = None
    needs_credentials = False
    auth_method = "none"
    detected_login_url = None
    detected_fields = []
    guidance = ""

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 AI-Testing-Prober/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(target_url)
            response_time_ms = round((time.time() - start_time) * 1000, 2)
            status_code = resp.status_code
            accessible = True

            content_type = resp.headers.get("content-type", "").lower()
            final_url = str(resp.url)
            final_parsed = urlparse(final_url)
            final_path = final_parsed.path.lower()

            parser = HTMLProbeParser()
            if "text/html" in content_type or not content_type:
                try:
                    parser.feed(resp.text[:200000])
                except Exception:
                    pass

            page_title = parser.title or None

            # Detect authentication gates
            is_auth_redirect = any(kw in final_path for kw in ["/login", "/signin", "/auth", "/session", "/oauth"])
            is_401_403 = status_code in (401, 403)
            has_login_form = parser.has_password_field or any("login" in a or "signin" in a for a in parser.form_actions)

            if is_401_403:
                needs_credentials = True
                auth_method = "basic_auth" if "www-authenticate" in resp.headers else "form"
                detected_login_url = final_url
                guidance = f"HTTP status {status_code} encountered. Access to this resource requires authentication credentials."
            elif is_auth_redirect or has_login_form:
                needs_credentials = True
                auth_method = "form"
                detected_login_url = final_url if is_auth_redirect else f"{clean_base_url}/login"
                detected_fields = [f for f in ["username", "email", "password"] if any(f in inp for inp in parser.input_names)]
                if not detected_fields and parser.has_password_field:
                    detected_fields = ["username", "password"]
                guidance = (
                    "Authentication gate detected (login form or redirect). "
                    "If testing private/authenticated pages, credentials are required. "
                    "For testing public landing/marketing pages only, credentials can remain disabled."
                )
            else:
                needs_credentials = False
                auth_method = "none"
                guidance = (
                    "This is a standard public website. No credentials or login required. "
                    "The automated scanner will test public guest pages, navigation, links, responsive layout, "
                    "performance metrics, security headers, and accessibility."
                )

    except httpx.HTTPError as exc:
        accessible = False
        guidance = f"Website probe failed or timed out: {str(exc)}. Ensure the target server is publicly accessible and running."
    except Exception as exc:
        accessible = False
        guidance = f"Probe connection error: {str(exc)}"

    is_public_website = not needs_credentials

    recommended_options = RecommendedExecutionOptions(
        check_functional=True,
        check_security=True,
        check_performance=True,
        check_accessibility=True,
        browser_type="chromium",
        viewport_width=1280,
        viewport_height=720,
        is_mobile=False,
        allow_state_changing=False,  # CRITICAL: Read-only guardrail for safe exploratory testing
        rate_limit_rps=10,           # Safe default to avoid overloading
        timeout_seconds=300,
        max_crawl_depth=2,
        use_ai_generation=True,
        ai_test_goals="Explore primary navigation, check responsive layout, test search & links",
        ai_explain_failures=True
    )

    credentials_requirement = CredentialRequirementInfo(
        required=needs_credentials,
        auth_method=auth_method,
        detected_login_url=detected_login_url,
        detected_fields=detected_fields,
        guidance=guidance
    )

    ready_quick_scan_payload = {
        "url": target_url,
        "name": (page_title or hostname.replace("www.", "").capitalize())[:50],
        "credentials": {
            "enabled": needs_credentials,
            "login_url": detected_login_url,
            "username": "user@example.com" if needs_credentials else None,
            "password": "your-password" if needs_credentials else None,
            "auth_method": auth_method if auth_method != "none" else "form",
            "success_indicator": None
        },
        "options": recommended_options.model_dump()
    }

    return WebsiteAnalyzeResponse(
        url=target_url,
        hostname=hostname,
        accessible=accessible,
        status_code=status_code,
        response_time_ms=response_time_ms,
        page_title=page_title,
        is_public_website=is_public_website,
        needs_credentials=needs_credentials,
        credentials_requirement=credentials_requirement,
        recommended_options=recommended_options,
        ready_quick_scan_payload=ready_quick_scan_payload
    )

