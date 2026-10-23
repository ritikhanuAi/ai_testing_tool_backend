from sqlalchemy import Column, String, Float, DateTime, Enum as SQLEnum, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.common.models import BaseModel
from app.common.constants import RunStatus

class TestRun(BaseModel):
    __tablename__ = "test_runs"

    website_id = Column(String(36), ForeignKey("websites.id"), nullable=False)
    scan_profile_id = Column(String(36), ForeignKey("scan_profiles.id"), nullable=True)
    test_pack_id = Column(String(36), ForeignKey("test_packs.id"), nullable=True)

    status = Column(SQLEnum(RunStatus), default=RunStatus.PENDING, nullable=False)
    celery_task_id = Column(String(255), nullable=True)
    overall_score = Column(Float, nullable=True)
    grade = Column(String(5), nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    report = relationship("ReportSummary", back_populates="test_run", uselist=False, cascade="all, delete-orphan")
