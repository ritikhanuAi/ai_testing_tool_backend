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
