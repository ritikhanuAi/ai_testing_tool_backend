import json
from typing import Optional, Tuple, Type, TypeVar
from pydantic import BaseModel
from test_engine.schema.case import TestCaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)

class SchemaEnforcer:
    """Extracts JSON from LLM response and validates against TestCaseModel with auto-retry loop."""

    @staticmethod
    def parse_model(raw_response: str, model_type: Type[ModelT]) -> Tuple[bool, Optional[ModelT], Optional[str]]:
        """Parse a JSON object from model output and validate it against a Pydantic contract."""
        try:
            cleaned = raw_response.strip()
            if cleaned.startswith("```") and cleaned.endswith("```"):
                cleaned = cleaned[3:-3].strip()
                if cleaned[:4].lower() == "json":
                    cleaned = cleaned[4:].strip()
            parsed_data = None
            decoder = json.JSONDecoder()
            for index, character in enumerate(cleaned):
                if character == "{":
                    try:
                        parsed_data, _ = decoder.raw_decode(cleaned[index:])
                        break
                    except json.JSONDecodeError:
                        continue
            if parsed_data is None:
                parsed_data = json.loads(cleaned)
            if not isinstance(parsed_data, dict):
                raise ValueError("AI response must be a JSON object")
            return True, model_type.model_validate(parsed_data), None
        except Exception as e:
            return False, None, f"AI output could not be parsed into valid {model_type.__name__}: {str(e)}"

    @classmethod
    def parse_test_case(cls, raw_response: str) -> Tuple[bool, Optional[TestCaseModel], Optional[str]]:
        """Parse and strictly validate an AI-generated test case."""
        return cls.parse_model(raw_response, TestCaseModel)

    @classmethod
    async def generate_and_validate_with_retry(
        cls,
        llm_client,
        system_prompt: str,
        initial_prompt: str,
        max_retries: int = 3,
        model_type: Type[ModelT] = TestCaseModel,
    ) -> Tuple[bool, Optional[ModelT], Optional[str]]:
        """Generate structured output and retry with validation feedback if its schema fails."""
        current_prompt = initial_prompt
        last_error = "Unknown error"

        for attempt in range(1, max_retries + 1):
            try:
                raw_response = await llm_client.generate(prompt=current_prompt, system_prompt=system_prompt)
                valid, parsed_model, error = cls.parse_model(raw_response, model_type)
                if valid and parsed_model:
                    return True, parsed_model, None
                last_error = error or "Schema validation failed"
                current_prompt = (
                    f"{initial_prompt}\n\n"
                    f"CRITICAL ERROR: Your previous response was invalid: {last_error}.\n"
                    f"Please correct the error and return ONLY valid JSON matching the exact schema."
                )
            except Exception as e:
                last_error = str(e)

        return False, None, f"Failed after {max_retries} attempts. Last error: {last_error}"
