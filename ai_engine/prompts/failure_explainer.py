import json
from typing import Dict, Any, Optional
from ai_engine.clients.factory import LLMClientFactory

EXPLAIN_SYSTEM_PROMPT = """You are an expert web developer and QA diagnostician.
Analyze the provided test case failure, step error, expected vs actual values, and provide:
1. Plain-language root cause of what broke and why.
2. Concrete recommended steps or code changes to resolve it.
3. Severity rating (CRITICAL, HIGH, MEDIUM, LOW).

Output ONLY valid JSON matching this schema:
{
  "root_cause": "Concise explanation of the bug or failure cause",
  "suggested_fix": "Concrete developer recommendation or code fix to resolve the issue",
  "severity": "HIGH"
}
Output raw JSON only with no surrounding commentary.
"""

def build_failure_explanation_prompt(
    test_title: str,
    failed_step: str,
    error_message: str,
    expected_value: str = "",
    actual_value: str = ""
) -> str:
    return f"""Test Case: {test_title}
Failed Step / Assertion: {failed_step}
Error: {error_message}
Expected: {expected_value}
Actual: {actual_value}

Diagnose the failure and return valid JSON with root_cause, suggested_fix, and severity.
"""

class AIFailureExplainer:
    """Uses Qwen-27B on STPI to diagnose functional test failures and generate developer fixes."""

    @classmethod
    async def explain_failure(
        cls,
        test_title: str,
        failed_step_or_assert: str,
        error_message: str,
        expected: str = "",
        actual: str = ""
    ) -> Dict[str, str]:
        prompt = build_failure_explanation_prompt(
            test_title=test_title,
            failed_step=failed_step_or_assert,
            error_message=error_message,
            expected_value=expected,
            actual_value=actual
        )
        try:
            client = LLMClientFactory.get_client()
            response = await client.generate(prompt=prompt, system_prompt=EXPLAIN_SYSTEM_PROMPT)
            cleaned = response.strip()
            if "{" in cleaned and "}" in cleaned:
                json_str = cleaned[cleaned.find("{"):cleaned.rfind("}")+1]
                data = json.loads(json_str)
                return {
                    "root_cause": data.get("root_cause", error_message),
                    "suggested_fix": data.get("suggested_fix", "Check form validation rules and backend response state."),
                    "severity": data.get("severity", "HIGH").upper()
                }
        except Exception:
            pass

        # Fallback if offline or timeout
        return {
            "root_cause": error_message,
            "suggested_fix": "Verify element locator specificity and page load state.",
            "severity": "HIGH"
        }
