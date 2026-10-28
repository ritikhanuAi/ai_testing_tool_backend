from sqlalchemy import Column, String, Text, Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship
from app.common.models import BaseModel, JSONType

class TestPack(BaseModel):
    __tablename__ = "test_packs"

    website_id = Column(String(36), ForeignKey("websites.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    version = Column(String(50), default="1.0.0", nullable=False)
    raw_yaml = Column(Text, nullable=False)
    cases_count = Column(Integer, default=0, nullable=False)
    is_valid = Column(Boolean, default=False, nullable=False)
    validation_errors = Column(JSONType, default=list, nullable=False)

    # Relationships
    website = relationship("Website", back_populates="test_packs")
    cases = relationship("TestCase", back_populates="test_pack", cascade="all, delete-orphan")
    test_runs = relationship("TestRun", back_populates="test_pack")

class TestCase(BaseModel):
    __tablename__ = "test_cases"
    __table_args__ = (
        UniqueConstraint("test_pack_id", "case_identifier", name="uq_test_cases_pack_identifier"),
    )

    test_pack_id = Column(String(36), ForeignKey("test_packs.id", ondelete="CASCADE"), nullable=False, index=True)
    case_identifier = Column(String(100), nullable=False)  # e.g. SITE-FUNC-001
    title = Column(String(255), nullable=False)
    pillar = Column(String(50), default="functional", nullable=False)
    risk_level = Column(String(50), default="read_only", nullable=False)
    tags = Column(JSONType, default=list, nullable=False)
    preconditions = Column(JSONType, default=list, nullable=False)
    steps = Column(JSONType, default=list, nullable=False)
    assertions = Column(JSONType, default=list, nullable=False)
    test_data = Column(JSONType, default=dict, nullable=False)
    credentials_ref = Column(String(100), nullable=True)
    order_index = Column(Integer, default=0, nullable=False)

    # Relationships
    test_pack = relationship("TestPack", back_populates="cases")
