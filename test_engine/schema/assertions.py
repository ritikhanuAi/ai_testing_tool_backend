from enum import Enum
from pydantic import BaseModel, Field, model_validator
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
    INPUT_INVALID = "input_invalid"
    FORM_INVALID = "form_invalid"
    PAGE_NO_SERVER_ERROR = "page_no_server_error"

class Assertion(BaseModel):
    type: AssertionType
    target: Optional[str] = Field(default=None, description="CSS selector or role locator")
    value: Optional[Any] = Field(default=None, description="Expected text, count, or code")
    description: Optional[str] = Field(default=None, description="Human readable assertion statement")

    @model_validator(mode="after")
    def validate_required_fields(self):
        target_required = {
            AssertionType.ELEMENT_VISIBLE,
            AssertionType.ELEMENT_HIDDEN,
            AssertionType.TEXT_CONTAINS,
            AssertionType.TEXT_EQUALS,
            AssertionType.OPTION_LIST_NOT_EMPTY,
            AssertionType.OPTION_COUNT_EQUALS,
            AssertionType.INPUT_INVALID,
            AssertionType.FORM_INVALID,
        }
        value_required = {
            AssertionType.TEXT_CONTAINS,
            AssertionType.TEXT_EQUALS,
            AssertionType.OPTION_COUNT_EQUALS,
            AssertionType.STATUS_CODE_EQUALS,
            AssertionType.URL_CONTAINS,
            AssertionType.PAGE_TITLE_CONTAINS,
        }
        if self.type in target_required and not self.target:
            raise ValueError(f"{self.type.value} requires a target")
        if self.type in value_required and self.value is None:
            raise ValueError(f"{self.type.value} requires a value")
        if self.type in {
            AssertionType.TEXT_CONTAINS,
            AssertionType.TEXT_EQUALS,
            AssertionType.URL_CONTAINS,
            AssertionType.PAGE_TITLE_CONTAINS,
        } and not str(self.value).strip():
            raise ValueError(f"{self.type.value} requires a non-empty value")
        if self.type in (AssertionType.OPTION_COUNT_EQUALS, AssertionType.STATUS_CODE_EQUALS):
            try:
                expected_number = int(self.value)
            except (TypeError, ValueError):
                raise ValueError(f"{self.type.value} requires an integer value") from None
            if self.type == AssertionType.OPTION_COUNT_EQUALS and expected_number < 0:
                raise ValueError("option_count_equals cannot use a negative count")
            if self.type == AssertionType.STATUS_CODE_EQUALS and not 100 <= expected_number <= 599:
                raise ValueError("status_code_equals must use an HTTP status from 100 to 599")
        return self
