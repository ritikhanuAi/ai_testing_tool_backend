import pytest
from test_engine.schema.validator import SchemaValidator
from app.common.constants import PillarType, RiskLevel

def test_validate_valid_case(sample_test_case_yaml):
    is_valid, case, errors = SchemaValidator.validate_yaml_case(sample_test_case_yaml)
    assert is_valid is True
    assert errors == []
    assert case is not None
    assert case.id == "SITE-FUNC-001"
    assert case.pillar == PillarType.FUNCTIONAL
    assert case.risk == RiskLevel.READ_ONLY
    assert len(case.steps) == 1
    assert case.steps[0].action.value == "select_option"
    assert len(case.assertions) == 1
    assert case.assertions[0].type.value == "option_list_not_empty"

def test_validate_valid_pack(sample_test_pack_yaml):
    is_valid, pack, errors = SchemaValidator.validate_yaml_pack(sample_test_pack_yaml)
    assert is_valid is True
    assert errors == []
    assert pack is not None
    assert pack.pack_id == "PACK-CORE-001"
    assert len(pack.cases) == 1

def test_validate_invalid_syntax():
    bad_yaml = "id: [unclosed list"
    is_valid, case, errors = SchemaValidator.validate_yaml_case(bad_yaml)
    assert is_valid is False
    assert len(errors) > 0
    assert "YAML syntax error" in errors[0]

def test_validate_missing_required_fields():
    missing_fields = """
id: TEST-001
# missing title, steps, assertions
"""
    is_valid, case, errors = SchemaValidator.validate_yaml_case(missing_fields)
    assert is_valid is False
    assert any("title" in err for err in errors)
    assert any("steps" in err for err in errors)
