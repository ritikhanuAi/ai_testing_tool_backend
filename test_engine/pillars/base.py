from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from app.common.constants import PillarType, FindingSeverity

import hashlib

class PillarFinding(BaseModel):
    id: str
    title: str
    description: str
    severity: FindingSeverity
    evidence_path: Optional[str] = None
    selector_or_url: Optional[str] = None
    suggested_fix: Optional[str] = None

    @property
    def fingerprint(self) -> str:
        """Deterministic SHA-1 fingerprint for tracking findings across test runs."""
        seed = f"{self.id}:{self.selector_or_url or ''}:{self.title}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]

class PillarScanResult(BaseModel):
    pillar: PillarType
    score: float = Field(..., ge=0, le=100, description="Pillar score from 0 to 100")
    status: str = Field(default="PASSED", description="PASSED, WARNING, FAILED")
    findings: List[PillarFinding] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    duration_sec: float = 0.0

class BasePillarScanner(ABC):
    """Abstract interface for all 4 quality pillar scanners."""

    @property
    @abstractmethod
    def pillar_type(self) -> PillarType:
        pass

    @abstractmethod
    async def scan(self, page, target_url: str, **kwargs) -> PillarScanResult:
        """Execute pillar checks and return structured findings and score."""
        pass
