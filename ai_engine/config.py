from pydantic import BaseModel
from app.core.config import settings

class AIEngineConfig(BaseModel):
    provider: str = settings.AI_PROVIDER
    ollama_base_url: str = settings.OLLAMA_BASE_URL
    ollama_model: str = settings.OLLAMA_MODEL
    timeout_seconds: int = settings.AI_TIMEOUT_SECONDS
    openai_api_key: str = settings.OPENAI_API_KEY or ""
    anthropic_api_key: str = settings.ANTHROPIC_API_KEY or ""

ai_config = AIEngineConfig()
