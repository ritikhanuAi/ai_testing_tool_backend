from sqlalchemy import Column, String, Float, DateTime, Enum as SQLEnum, ForeignKey, Text, Integer, UniqueConstraint
from sqlalchemy.orm import relationship
from app.common.models import BaseModel, JSONType
from app.common.constants import RunStatus

class TestRun(BaseModel):
    __tablename__ = "test_runs"

    website_id = Column(String(36), ForeignKey("websites.id", ondelete="CASCADE"), nullable=False, index=True)
    scan_profile_id = Column(String(36), ForeignKey("scan_profiles.id", ondelete="SET NULL"), nullable=True)
    test_pack_id = Column(String(36), ForeignKey("test_packs.id", ondelete="SET NULL"), nullable=True)

    status = Column(SQLEnum(RunStatus), default=RunStatus.PENDING, nullable=False, index=True)
    celery_task_id = Column(String(255), nullable=True)
    overall_score = Column(Float, nullable=True)
    grade = Column(String(5), nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    @property
    def finished_at(self):
        return self.completed_at

    @finished_at.setter
    def finished_at(self, value):
        self.completed_at = value

    # Relationships
    website = relationship("Website", back_populates="test_runs")
    scan_profile = relationship("ScanProfile", back_populates="test_runs")
    test_pack = relationship("TestPack", back_populates="test_runs")
    pillar_results = relationship("PillarResult", back_populates="test_run", cascade="all, delete-orphan")
    findings = relationship("TestFinding", back_populates="test_run", cascade="all, delete-orphan")
    artifacts = relationship("RunArtifact", back_populates="test_run", cascade="all, delete-orphan")
    report = relationship("ReportSummary", back_populates="test_run", uselist=False, cascade="all, delete-orphan")

class PillarResult(BaseModel):
    __tablename__ = "pillar_results"
    __table_args__ = (
        UniqueConstraint("test_run_id", "pillar", name="uq_pillar_results_run_pillar"),
    )

    test_run_id = Column(String(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    pillar = Column(String(50), nullable=False)
    score = Column(Float, nullable=False)
    status = Column(String(50), default="PASSED", nullable=False)
    duration_sec = Column(Float, default=0.0, nullable=False)
    findings_count = Column(Integer, default=0, nullable=False)
    metrics = Column(JSONType, default=dict, nullable=False)

    # Relationships
    test_run = relationship("TestRun", back_populates="pillar_results")
    findings = relationship("TestFinding", back_populates="pillar_result", cascade="all, delete-orphan")

class TestFinding(BaseModel):
    __tablename__ = "test_findings"

    test_run_id = Column(String(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    pillar_result_id = Column(String(36), ForeignKey("pillar_results.id", ondelete="CASCADE"), nullable=True, index=True)
    rule_id = Column(String(100), nullable=False)
    fingerprint = Column(String(32), index=True, nullable=False)
    pillar = Column(String(50), index=True, nullable=False)
    severity = Column(String(50), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    selector_or_url = Column(String(500), nullable=True)
    suggested_fix = Column(Text, nullable=True)
    evidence_path = Column(String(500), nullable=True)

    # Relationships
    test_run = relationship("TestRun", back_populates="findings")
    pillar_result = relationship("PillarResult", back_populates="findings")

class RunArtifact(BaseModel):
    __tablename__ = "run_artifacts"

    test_run_id = Column(String(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_type = Column(String(50), nullable=False)  # screenshot, trace, har, video, html_snapshot
    relative_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_size_bytes = Column(Integer, default=0, nullable=False)
    mime_type = Column(String(100), default="image/png", nullable=False)

    # Relationships
    test_run = relationship("TestRun", back_populates="artifacts")
