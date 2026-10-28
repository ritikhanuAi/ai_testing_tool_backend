from pydantic import BaseModel
from app.core.config import settings

class AIEngineConfig(BaseModel):
    provider: str = settings.AI_PROVIDER
    ollama_base_url: str = settings.OLLAMA_BASE_URL
    ollama_model: str = settings.OLLAMA_MODEL


    open_base_url: str =getattr(settings, "OPEN_BASE_URL", "https://18e4-203-190-154-12.ngrok-free.app/v1")
    open_api_key: str = getattr(settings, "OPEN_API_KEY", "UP1STC5VBzLcADzIzOrK9LhXgJo6ko14s7XAA3AuKpQ")
    openai_model: str = getattr(settings, "OPENAI_MODEL", "unsloth/Qwen3.8-27B-GGUF")

    timeout_seconds: int = settings.AI_TIMEOUT_SECONDS
    anthropic_api_key: str = settings.ANTHROPIC_API_KEY or ""

ai_config = AIEngineConfig()
