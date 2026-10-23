from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from app.common.constants import PillarType, RiskLevel
from test_engine.schema.actions import ActionStep
from test_engine.schema.assertions import Assertion

class TestCaseModel(BaseModel):
    __test__ = False
    id: str = Field(..., description="Stable test case ID, e.g. SITE-FUNC-001")
    title: str = Field(..., description="Short name of what the test verifies")
    pillar: PillarType = Field(default=PillarType.FUNCTIONAL)
    tags: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list, description="e.g. ['test_user_is_signed_in']")
    steps: List[ActionStep] = Field(..., description="List of supported browser action steps")
    assertions: List[Assertion] = Field(..., description="List of conditions that must pass")
    test_data: Optional[Dict[str, Any]] = Field(default_factory=dict)
    credentials_ref: Optional[str] = Field(default=None, description="Key name of stored credentials")
    risk: RiskLevel = Field(default=RiskLevel.READ_ONLY, description="'read_only' or 'state_changing'")
    cleanup: Optional[List[ActionStep]] = Field(default_factory=list, description="Safe cleanup steps")
    evidence: List[str] = Field(default_factory=lambda: ["screenshot", "assertion_log"])

class TestPackModel(BaseModel):
    __test__ = False
    pack_id: str = Field(..., description="Identifier for this test pack")
    title: str = Field(..., description="Human friendly title")
    version: str = Field(default="1.0.0", description="Semantic version of pack")
    target_domain: Optional[str] = Field(default=None, description="Associated website domain")
    cases: List[TestCaseModel] = Field(..., description="List of test cases included in this pack")
