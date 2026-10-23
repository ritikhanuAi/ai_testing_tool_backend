from pydantic import BaseModel, Field

class EngineConfig(BaseModel):
    headless: bool = True
    browser_type: str = "chromium"
    default_timeout_ms: int = 30000
    action_timeout_ms: int = 10000
    viewport_width: int = 1280
    viewport_height: int = 720
    mobile_viewport_width: int = 375
    mobile_viewport_height: int = 667
    user_agent: str = "AITestingPlatform/1.0 (+https://example.com/bot)"
    trace_on_failure: bool = True
    screenshot_on_failure: bool = True

default_engine_config = EngineConfig()
