import json
import re
from typing import Optional, Tuple
from test_engine.schema.case import TestCaseModel

class SchemaEnforcer:
    """Extracts JSON from LLM response and validates against TestCaseModel with auto-retry loop."""

    @staticmethod
    def parse_test_case(raw_response: str) -> Tuple[bool, Optional[TestCaseModel], Optional[str]]:
        """Parse and strictly validate an AI-generated test case."""
        try:
            # Extract JSON from potential markdown code fences
            cleaned = raw_response.strip()
            fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
            if fence_match:
                json_str = fence_match.group(1)
            else:
                brace_match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
                json_str = brace_match.group(1) if brace_match else cleaned

            parsed_data = json.loads(json_str)
            case_model = TestCaseModel.model_validate(parsed_data)
            return True, case_model, None
        except Exception as e:
            return False, None, f"AI output could not be parsed into valid TestCase: {str(e)}"

    @classmethod
    async def generate_and_validate_with_retry(
        cls,
        llm_client,
        system_prompt: str,
        initial_prompt: str,
        max_retries: int = 3
    ) -> Tuple[bool, Optional[TestCaseModel], Optional[str]]:
        """Generate test case and automatically retry with validation error feedback if schema fails."""
        current_prompt = initial_prompt
        last_error = "Unknown error"

        for attempt in range(1, max_retries + 1):
            try:
                raw_response = await llm_client.generate(prompt=current_prompt, system_prompt=system_prompt)
                valid, case_model, error = cls.parse_test_case(raw_response)
                if valid and case_model:
                    return True, case_model, None
                last_error = error or "Schema validation failed"
                current_prompt = (
                    f"{initial_prompt}\n\n"
                    f"CRITICAL ERROR: Your previous response was invalid: {last_error}.\n"
                    f"Please correct the error and return ONLY valid JSON matching the exact schema."
                )
            except Exception as e:
                last_error = str(e)

        return False, None, f"Failed after {max_retries} attempts. Last error: {last_error}"
