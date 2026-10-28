from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional
from datetime import datetime
from app.common.serializers import BaseSchema
from app.common.constants import TargetEnvironment, VerificationStatus

class WebsiteCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    base_url: str = Field(..., max_length=500)
    environment: TargetEnvironment = TargetEnvironment.STAGING
    allowed_domains: List[str] = Field(default_factory=list)

class WebsiteResponse(BaseSchema):
    id: str
    name: str
    base_url: str
    environment: TargetEnvironment
    allowed_domains: List[str]
    is_authorized: bool
    is_active: bool
    verification_token: Optional[str] = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    verified_at: Optional[datetime] = None

class WebsiteVerificationResponse(BaseSchema):
    website_id: str
    status: VerificationStatus
    verified: bool
    message: str
    verification_token: Optional[str] = None
    dns_txt_instruction: str
    http_file_instruction: str

class CredentialCreateRequest(BaseModel):
    key_name: str = Field(..., max_length=100)
    username: Optional[str] = None
    secret: str = Field(..., description="Plain secret/password to be encrypted immediately")
    description: Optional[str] = None

class CredentialResponse(BaseSchema):
    id: str
    website_id: str
    key_name: str
    username: Optional[str] = None
    description: Optional[str] = None

class WebsiteAnalyzeRequest(BaseModel):
    url: str = Field(..., description="Target website entry URL (e.g. https://example.com)")

class CredentialRequirementInfo(BaseModel):
    required: bool = False
    auth_method: str = "none"  # "none", "form", "basic_auth"
    detected_login_url: Optional[str] = None
    detected_fields: List[str] = Field(default_factory=list)
    guidance: str

class RecommendedExecutionOptions(BaseModel):
    check_functional: bool = True
    check_security: bool = True
    check_performance: bool = True
    check_accessibility: bool = True
    browser_type: str = "chromium"
    viewport_width: int = 1280
    viewport_height: int = 720
    is_mobile: bool = False
    allow_state_changing: bool = False
    rate_limit_rps: int = 10
    timeout_seconds: int = 300
    max_crawl_depth: int = 2
    use_ai_generation: bool = True
    ai_test_goals: Optional[str] = None
    ai_explain_failures: bool = True

class WebsiteAnalyzeResponse(BaseSchema):
    url: str
    hostname: str
    accessible: bool
    status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    page_title: Optional[str] = None
    is_public_website: bool
    needs_credentials: bool
    credentials_requirement: CredentialRequirementInfo
    recommended_options: RecommendedExecutionOptions
    ready_quick_scan_payload: dict

