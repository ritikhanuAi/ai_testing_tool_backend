from sqlalchemy import Column, String, Float, Boolean, Integer, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.common.models import BaseModel

class ReportSummary(BaseModel):
    __tablename__ = "reports"

    test_run_id = Column(String(36), ForeignKey("test_runs.id"), unique=True, nullable=False)

    overall_score = Column(Float, nullable=False)
    grade = Column(String(5), nullable=False)
    passed = Column(Boolean, default=True, nullable=False)

    # 4 Pillar Breakdown Scores
    functional_score = Column(Float, nullable=False)
    security_score = Column(Float, nullable=False)
    performance_score = Column(Float, nullable=False)
    accessibility_score = Column(Float, nullable=False)

    total_findings = Column(Integer, default=0, nullable=False)
    critical_findings = Column(Integer, default=0, nullable=False)
    high_findings = Column(Integer, default=0, nullable=False)

    # Full structured report JSON (including metrics, screenshots, steps)
    report_data = Column(JSON, nullable=False)

    test_run = relationship("TestRun", back_populates="report")
