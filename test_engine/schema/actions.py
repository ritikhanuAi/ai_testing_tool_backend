from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional

class ActionType(str, Enum):
    OPEN_PAGE = "open_page"
    CLICK = "click"
    SELECT_OPTION = "select_option"
    TYPE_TEXT = "type_text"
    WAIT = "wait"
    DOWNLOAD = "download"
    SCROLL = "scroll"
    HOVER = "hover"

class ActionStep(BaseModel):
    action: ActionType
    target: Optional[str] = Field(default=None, description="CSS selector or Playwright role locator (e.g. role=combobox[name='Region'])")
    fallback_role: Optional[str] = Field(default=None, description="Accessible role used to recover a stale selector")
    fallback_name: Optional[str] = Field(default=None, description="Accessible name used with fallback_role")
    value: Optional[str] = Field(default=None, description="Input text, option value, or URL")
    timeout_ms: Optional[int] = Field(default=10000, description="Step timeout in milliseconds")
    description: Optional[str] = Field(default=None, description="Human readable description of the step")
