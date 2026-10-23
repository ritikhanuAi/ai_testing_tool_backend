from sqlalchemy import Column, String, Text, Boolean, JSON, ForeignKey, Integer
from sqlalchemy.orm import relationship
from app.common.models import BaseModel

class TestPack(BaseModel):
    __tablename__ = "test_packs"

    website_id = Column(String(36), ForeignKey("websites.id"), nullable=True)
    title = Column(String(255), nullable=False)
    version = Column(String(50), default="1.0.0", nullable=False)
    raw_yaml = Column(Text, nullable=False)
    cases_count = Column(Integer, default=0, nullable=False)
    is_valid = Column(Boolean, default=False, nullable=False)
    validation_errors = Column(JSON, default=list, nullable=False)
