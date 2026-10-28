from fastapi import APIRouter, status
from typing import List
from app.common.serializers import ResponseEnvelope
from app.apps.websites.serializers import (
    WebsiteResponse, CredentialResponse, WebsiteVerificationResponse, WebsiteAnalyzeResponse
)
from app.apps.websites.views import (
    list_websites_view, create_website_view, get_website_view, add_credential_view,
    verify_website_view, analyze_website_view
)

router = APIRouter(prefix="/websites", tags=["Websites"])

router.post("/analyze", response_model=ResponseEnvelope[WebsiteAnalyzeResponse], summary="Analyze Website URL for Testing Requirements")(analyze_website_view)
router.get("", response_model=ResponseEnvelope[List[WebsiteResponse]], summary="List Websites")(list_websites_view)
router.post("", response_model=ResponseEnvelope[WebsiteResponse], status_code=status.HTTP_201_CREATED, summary="Create Website")(create_website_view)
router.get("/{website_id}", response_model=ResponseEnvelope[WebsiteResponse], summary="Get Website By ID")(get_website_view)
router.post("/{website_id}/credentials", response_model=ResponseEnvelope[CredentialResponse], status_code=status.HTTP_201_CREATED, summary="Add Credential to Website")(add_credential_view)
router.post("/{website_id}/verify", response_model=ResponseEnvelope[WebsiteVerificationResponse], summary="Verify Website Domain Ownership")(verify_website_view)

