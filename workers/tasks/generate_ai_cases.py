import asyncio
import logging
import time
from typing import Dict, Any, Optional
from workers.celery_app import celery_app
from ai_engine.clients.factory import LLMClientFactory
from ai_engine.prompts.test_generation import SYSTEM_PROMPT, build_case_generation_prompt
from ai_engine.parsers.schema_enforcer import SchemaEnforcer

logger = logging.getLogger("workers.tasks.generate_ai_cases")

async def _async_generate_cases(page_description: str, form_fields: str = "") -> Dict[str, Any]:
    client = LLMClientFactory.get_client()
    prompt = build_case_generation_prompt(page_description, form_fields)

    started = time.perf_counter()
    valid, case_model, error = await SchemaEnforcer.generate_and_validate_with_retry(
        llm_client=client,
        system_prompt=SYSTEM_PROMPT,
        initial_prompt=prompt,
    )
    ai_seconds = round(time.perf_counter() - started, 2)

    if valid and case_model:
        logger.info(f"AI test case timing: test case '{case_model.id}' written in {ai_seconds:.1f}s")
        return {"success": True, "case": case_model.model_dump(), "ai_seconds": ai_seconds}
    logger.warning(f"AI test case timing: no valid test case after {ai_seconds:.1f}s: {error}")
    return {"success": False, "error": error, "raw": None, "ai_seconds": ai_seconds}

@celery_app.task(name="workers.tasks.generate_ai_cases.generate_test_case_task")
def generate_test_case_task(page_description: str, form_fields: str = ""):
    """Async background task for LLM test case generation."""
    logger.info("Generating AI test case in background...")
    return asyncio.run(_async_generate_cases(page_description, form_fields))
