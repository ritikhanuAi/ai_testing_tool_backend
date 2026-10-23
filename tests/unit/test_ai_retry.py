import pytest
from unittest.mock import AsyncMock
from ai_engine.parsers.schema_enforcer import SchemaEnforcer

@pytest.mark.asyncio
async def test_schema_enforcer_retry_success():
    """Verify that SchemaEnforcer automatically retries upon initial schema failure and succeeds."""
    mock_llm = AsyncMock()
    # Attempt 1 returns broken JSON, Attempt 2 returns valid JSON
    mock_llm.generate.side_effect = [
        "Not valid json at all",
        '{"id": "TEST-01", "title": "Mock Test", "steps": [{"action": "open_page", "target": "https://example.com"}], "assertions": [{"type": "status_code_equals", "value": 200}]}'
    ]

    success, case, error = await SchemaEnforcer.generate_and_validate_with_retry(
        llm_client=mock_llm,
        system_prompt="system",
        initial_prompt="initial",
        max_retries=3
    )

    assert success is True
    assert case is not None
    assert case.id == "TEST-01"
    assert mock_llm.generate.call_count == 2
