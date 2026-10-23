from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, Any

class AssertionType(str, Enum):
    ELEMENT_VISIBLE = "element_visible"
    ELEMENT_HIDDEN = "element_hidden"
    TEXT_CONTAINS = "text_contains"
    TEXT_EQUALS = "text_equals"
    OPTION_LIST_NOT_EMPTY = "option_list_not_empty"
    OPTION_COUNT_EQUALS = "option_count_equals"
    STATUS_CODE_EQUALS = "status_code_equals"
    URL_CONTAINS = "url_contains"
    PAGE_TITLE_CONTAINS = "page_title_contains"

class Assertion(BaseModel):
    type: AssertionType
    target: Optional[str] = Field(default=None, description="CSS selector or role locator")
    value: Optional[Any] = Field(default=None, description="Expected text, count, or code")
    description: Optional[str] = Field(default=None, description="Human readable assertion statement")
