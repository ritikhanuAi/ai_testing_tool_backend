import pytest
from ai_engine.parsers.schema_enforcer import SchemaEnforcer

def test_parse_json_with_code_fences():
    raw_llm = """
Here is the requested test case:
```json
{
  "id": "SITE-FUNC-005",
  "title": "Validate search box",
  "pillar": "functional",
  "steps": [
    {"action": "open_page", "value": "/search"},
    {"action": "type_text", "target": "#q", "value": "laptops"}
  ],
  "assertions": [
    {"type": "element_visible", "target": ".results-grid"}
  ],
  "risk": "read_only"
}
```
Hope this helps!
"""
    success, case, error = SchemaEnforcer.parse_test_case(raw_llm)
    assert success is True
    assert error is None
    assert case is not None
    assert case.id == "SITE-FUNC-005"
    assert len(case.steps) == 2

def test_parse_invalid_json():
    raw_llm = "Sorry, I cannot generate test cases without a URL."
    success, case, error = SchemaEnforcer.parse_test_case(raw_llm)
    assert success is False
    assert case is None
    assert "could not be parsed" in error
