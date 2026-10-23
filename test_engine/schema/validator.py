import yaml
import json
from typing import Tuple, List, Optional
from pydantic import ValidationError
from test_engine.schema.case import TestPackModel, TestCaseModel

class SchemaValidator:
    """Validates raw YAML or JSON test definitions against Pydantic schema."""

    @staticmethod
    def validate_yaml_pack(yaml_content: str) -> Tuple[bool, Optional[TestPackModel], List[str]]:
        """Validate a full test pack from YAML string."""
        errors = []
        try:
            parsed_data = yaml.safe_load(yaml_content)
            if not isinstance(parsed_data, dict):
                return False, None, ["YAML content must be a dictionary / mapping"]
            pack = TestPackModel.model_validate(parsed_data)
            return True, pack, []
        except yaml.YAMLError as ye:
            return False, None, [f"YAML syntax error: {str(ye)}"]
        except ValidationError as ve:
            for err in ve.errors():
                loc = " -> ".join(str(p) for p in err["loc"])
                errors.append(f"[{loc}]: {err['msg']}")
            return False, None, errors
        except Exception as e:
            return False, None, [f"Unexpected error: {str(e)}"]

    @staticmethod
    def validate_yaml_case(yaml_content: str) -> Tuple[bool, Optional[TestCaseModel], List[str]]:
        """Validate a single test case from YAML string."""
        errors = []
        try:
            parsed_data = yaml.safe_load(yaml_content)
            if not isinstance(parsed_data, dict):
                return False, None, ["YAML content must be a dictionary / mapping"]
            case = TestCaseModel.model_validate(parsed_data)
            return True, case, []
        except yaml.YAMLError as ye:
            return False, None, [f"YAML syntax error: {str(ye)}"]
        except ValidationError as ve:
            for err in ve.errors():
                loc = " -> ".join(str(p) for p in err["loc"])
                errors.append(f"[{loc}]: {err['msg']}")
            return False, None, errors
        except Exception as e:
            return False, None, [f"Unexpected error: {str(e)}"]
