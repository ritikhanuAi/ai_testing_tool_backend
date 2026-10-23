from app.common.constants import RiskLevel
from test_engine.schema.case import TestCaseModel
from typing import Tuple

class RiskPolicyChecker:
    """Checks whether test cases conform to the authorization level of the scan profile."""

    @staticmethod
    def is_execution_permitted(case: TestCaseModel, allow_state_changing: bool) -> Tuple[bool, str]:
        """Check if a test case is permitted to run under the active scan profile."""
        if case.risk == RiskLevel.STATE_CHANGING and not allow_state_changing:
            return False, f"Test case '{case.id}' requires 'state_changing' write permission, but current scan profile is READ-ONLY."
        return True, "Permitted"
