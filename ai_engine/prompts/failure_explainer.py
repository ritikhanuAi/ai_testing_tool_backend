EXPLAIN_SYSTEM_PROMPT = """You are an expert web developer and QA diagnostician.
Analyze the provided test case failure, step error, expected vs actual values, and provide:
1. A plain-language summary of what broke and why.
2. The most likely root cause in frontend or backend code.
3. Concrete recommended steps to resolve it.
Keep your response concise, professional, and actionable.
"""

def build_failure_explanation_prompt(
    test_title: str,
    failed_step: str,
    error_message: str,
    expected_value: str,
    actual_value: str
) -> str:
    return f"""Test Case: {test_title}
Failed Step: {failed_step}
Error: {error_message}
Expected: {expected_value}
Actual: {actual_value}

Explain why this test failed and provide clear steps to fix the issue.
"""
