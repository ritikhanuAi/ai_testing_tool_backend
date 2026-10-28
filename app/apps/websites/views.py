from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.core.database import get_db
from app.common.serializers import ResponseEnvelope
from app.apps.websites.serializers import (
    WebsiteCreateRequest, WebsiteResponse, CredentialCreateRequest, CredentialResponse,
    WebsiteVerificationResponse, WebsiteAnalyzeRequest, WebsiteAnalyzeResponse
)
from app.apps.websites.services import create_website, add_credential, analyze_target_url
from app.apps.websites.selectors import list_websites, get_website_by_id

async def analyze_website_view(
    data: WebsiteAnalyzeRequest
) -> ResponseEnvelope[WebsiteAnalyzeResponse]:
    """Probe website URL to determine testing requirements and recommended execution options."""
    result = await analyze_target_url(data)
    return ResponseEnvelope(
        message="Website requirements analyzed successfully",
        data=result
    )

async def list_websites_view(
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[List[WebsiteResponse]]:
    sites = await list_websites(db)
    items = [WebsiteResponse.model_validate(s) for s in sites]
    return ResponseEnvelope(
        message="Websites retrieved successfully",
        data=items,
        meta={"count": len(items)}
    )

async def create_website_view(
    data: WebsiteCreateRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[WebsiteResponse]:
    site = await create_website(db, data)
    return ResponseEnvelope(
        message="Website registered successfully",
        data=WebsiteResponse.model_validate(site)
    )

async def get_website_view(
    website_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[WebsiteResponse]:
    site = await get_website_by_id(db, website_id)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
    return ResponseEnvelope(
        message="Website details retrieved successfully",
        data=WebsiteResponse.model_validate(site)
    )

async def add_credential_view(
    website_id: str,
    data: CredentialCreateRequest,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[CredentialResponse]:
    site = await get_website_by_id(db, website_id)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website not found")
    cred = await add_credential(db, website_id, data)
    return ResponseEnvelope(
        message="Credential stored securely",
        data=CredentialResponse.model_validate(cred)
    )

async def verify_website_view(
    website_id: str,
    db: AsyncSession = Depends(get_db)
) -> ResponseEnvelope[WebsiteVerificationResponse]:
    from app.apps.websites.services import verify_website_ownership
    result = await verify_website_ownership(db, website_id)
    return ResponseEnvelope(
        message=result["message"],
        data=WebsiteVerificationResponse.model_validate(result)
    )
