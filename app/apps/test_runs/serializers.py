import json
import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator
from typing import Optional, List, Any, Union
from datetime import datetime
from app.common.serializers import BaseSchema
from app.common.constants import RunStatus
from test_engine.schema.case import TestCaseModel

class AuthenticationPayload(BaseModel):
    enabled: bool = False
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None  # Encrypted immediately upon receipt
    auth_method: str = "form"       # "form", "basic_auth", or "storage_state"
    success_indicator: Optional[str] = None  # e.g., selector "#dashboard" or URL regex "/app"

class AdvancedExecutionOptions(BaseModel):
    # 4 Pillars Selection
    check_functional: bool = True
    check_security: bool = True
    check_performance: bool = True
    check_accessibility: bool = True

    # Browser & Emulation Settings
    browser_type: str = "chromium"      # "chromium", "firefox", "webkit"
    viewport_width: int = 1280
    viewport_height: int = 720
    is_mobile: bool = False
    record_trace: bool = True           # Saves Playwright trace.zip
    record_video: bool = False

    # Safety Guardrails & Rate Limits
    allow_state_changing: bool = False  # False = Read-only mode (blocks destructive actions)
    rate_limit_rps: int = 10            # Max HTTP requests per second to target
    timeout_seconds: int = 300          # Total scan timeout
    max_crawl_depth: int = 2            # Exploration depth

    # AI Intelligence Options
    use_ai_generation: bool = True      # Use LLM to author tests dynamically
    ai_test_goals: Optional[str] = None # Natural language intent (e.g. "Test checkout & signup")
    max_ai_cases: int = 5               # Maximum tests for AI to generate
    ai_explain_failures: bool = True    # Use AI to diagnose root causes of defect findings

class QuickScanRequest(BaseModel):
    url: str = Field(..., description="Target website entry URL (e.g. https://example.com)")
    name: Optional[str] = Field(default=None, description="Optional website label")
    credentials: Optional[AuthenticationPayload] = None
    options: Optional[AdvancedExecutionOptions] = Field(default_factory=AdvancedExecutionOptions)

class CustomScanRequest(BaseModel):
    url: str = Field(..., description="Target website entry URL (e.g. https://example.com)")
    name: Optional[str] = Field(default=None, description="Optional suite or website label")
    credentials: Optional[AuthenticationPayload] = None
    options: Optional[AdvancedExecutionOptions] = Field(default_factory=AdvancedExecutionOptions)
    cases: List[TestCaseModel] = Field(..., min_length=1, description="List of structured test cases to execute")

    @field_validator("cases", mode="before")
    @classmethod
    def normalize_cases(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                try:
                    v = yaml.safe_load(v)
                except Exception as e:
                    raise ValueError(f"Could not parse cases JSON/YAML string: {e}")

        if isinstance(v, dict):
            if "cases" in v and isinstance(v["cases"], list):
                v = v["cases"]
            else:
                raise ValueError("Dictionary must contain a 'cases' list.")

        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("Must provide at least one test case in 'cases' list.")

        return v

class TriggerScanRequest(BaseModel):
    website_id: str
    scan_profile_id: Optional[str] = None
    test_pack_id: Optional[str] = None

class TestRunResponse(BaseSchema):
    id: str
    website_id: str
    scan_profile_id: Optional[str] = None
    test_pack_id: Optional[str] = None
    status: RunStatus
    celery_task_id: Optional[str] = None
    overall_score: Optional[float] = None
    grade: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

