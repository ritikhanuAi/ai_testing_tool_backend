from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional

class ActionType(str, Enum):
    OPEN_PAGE = "open_page"
    CLICK = "click"
    SELECT_OPTION = "select_option"
    SET_CHECKED = "set_checked"
    SET_RANGE = "set_range"
    TYPE_TEXT = "type_text"
    UPLOAD_FILE = "upload_file"
    WAIT = "wait"
    DOWNLOAD = "download"
    SCROLL = "scroll"
    HOVER = "hover"
    RELOAD_PAGE = "reload_page"

class ActionStep(BaseModel):
    action: ActionType
    target: Optional[str] = Field(default=None, description="CSS selector, XPath (e.g. //input[@id='email']), or role locator (e.g. role=combobox[name='Region'])")
    fallback_role: Optional[str] = Field(default=None, description="Accessible role used to recover a stale selector")
    fallback_name: Optional[str] = Field(default=None, description="Accessible name used with fallback_role")
    value: Optional[str] = Field(default=None, description="Input text, option value, or URL")
    select_by_value: bool = Field(default=False, description="For native selects, match the option value instead of its label")
    timeout_ms: Optional[int] = Field(default=10000, description="Step timeout in milliseconds")
    description: Optional[str] = Field(default=None, description="Human readable description of the step")
