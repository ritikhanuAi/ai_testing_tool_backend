from sqlalchemy import Column, String, Boolean, Enum as SQLEnum, JSON, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from app.common.models import BaseModel
from app.common.constants import TargetEnvironment, VerificationStatus

class Website(BaseModel):
    __tablename__ = "websites"

    name = Column(String(255), nullable=False)
    base_url = Column(String(500), nullable=False)
    environment = Column(SQLEnum(TargetEnvironment), default=TargetEnvironment.STAGING, nullable=False)
    allowed_domains = Column(JSON, default=list, nullable=False)
    is_authorized = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    verification_token = Column(String(64), nullable=True)
    verification_status = Column(SQLEnum(VerificationStatus), default=VerificationStatus.PENDING, nullable=False)
    verified_at = Column(DateTime, nullable=True)

    # Relationships
    credentials = relationship("CredentialRef", back_populates="website", cascade="all, delete-orphan")

class CredentialRef(BaseModel):
    __tablename__ = "credentials"

    website_id = Column(String(36), ForeignKey("websites.id"), nullable=False)
    key_name = Column(String(100), nullable=False)  # Reference name used in YAML test cases
    username = Column(String(255), nullable=True)
    encrypted_secret = Column(String(1000), nullable=False)  # Fernet encrypted password / token
    description = Column(String(255), nullable=True)

    website = relationship("Website", back_populates="credentials")
