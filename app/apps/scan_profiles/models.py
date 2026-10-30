from sqlalchemy import Column, String, Boolean, Integer
from sqlalchemy.orm import relationship
from app.common.models import BaseModel

class ScanProfile(BaseModel):
    __tablename__ = "scan_profiles"

    name = Column(String(255), unique=True, nullable=False)
    description = Column(String(500), nullable=True)

    # 4 Pillars toggles
    check_functional = Column(Boolean, default=True, nullable=False)
    check_security = Column(Boolean, default=True, nullable=False)
    check_performance = Column(Boolean, default=True, nullable=False)
    check_accessibility = Column(Boolean, default=True, nullable=False)

    # Safety limits
    allow_state_changing = Column(Boolean, default=False, nullable=False)  # Read-only default!
    rate_limit = Column(Integer, default=10, nullable=False)  # Max req/sec
    timeout_seconds = Column(Integer, default=900, nullable=False)

    # Browser Configuration
    browser_type = Column(String(50), default="chromium", nullable=False)
    viewport_width = Column(Integer, default=1280, nullable=False)
    viewport_height = Column(Integer, default=720, nullable=False)

    # Relationships
    test_runs = relationship("TestRun", back_populates="scan_profile")
