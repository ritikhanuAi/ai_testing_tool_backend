import asyncio
import logging
from typing import Dict, Any, Optional
from workers.celery_app import celery_app
from ai_engine.clients.factory import LLMClientFactory
from ai_engine.prompts.test_generation import SYSTEM_PROMPT, build_case_generation_prompt
from ai_engine.parsers.schema_enforcer import SchemaEnforcer

logger = logging.getLogger("workers.tasks.generate_ai_cases")

async def _async_generate_cases(page_description: str, form_fields: str = "") -> Dict[str, Any]:
    client = LLMClientFactory.get_client()
    prompt = build_case_generation_prompt(page_description, form_fields)

    valid, case_model, error = await SchemaEnforcer.generate_and_validate_with_retry(
        llm_client=client,
        system_prompt=SYSTEM_PROMPT,
        initial_prompt=prompt,
    )

    if valid and case_model:
        return {"success": True, "case": case_model.model_dump()}
    else:
        return {"success": False, "error": error, "raw": None}

@celery_app.task(name="workers.tasks.generate_ai_cases.generate_test_case_task")
def generate_test_case_task(page_description: str, form_fields: str = ""):
    """Async background task for LLM test case generation."""
    logger.info("Generating AI test case in background...")
    return asyncio.run(_async_generate_cases(page_description, form_fields))
