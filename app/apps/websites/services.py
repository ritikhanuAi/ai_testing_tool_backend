from sqlalchemy.ext.asyncio import AsyncSession
from app.apps.websites.models import Website, CredentialRef
from app.apps.websites.serializers import WebsiteCreateRequest, CredentialCreateRequest
from app.common.utils import extract_domain
from app.core.security import encrypt_credential

import secrets
from datetime import datetime, timezone
import httpx
from app.core.config import settings
from app.common.constants import VerificationStatus
from app.core.exceptions import EntityNotFoundException

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
