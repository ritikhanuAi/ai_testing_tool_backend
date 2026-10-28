import os
from pydantic import BaseModel
from app.core.config import settings

class AIEngineConfig(BaseModel):
    provider: str = os.getenv("AI_PROVIDER", getattr(settings, "AI_PROVIDER", "stpi"))
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = os.getenv("OLLAMA_MODEL", getattr(settings, "OLLAMA_MODEL", "qwen2.5:latest"))

    open_base_url: str = os.getenv("OPEN_BASE_URL", getattr(settings, "OPEN_BASE_URL", "https://stpi.roadathena.com/v1"))
    open_api_key: str = os.getenv("OPEN_API_KEY", getattr(settings, "OPEN_API_KEY", "hanuai@321"))
    openai_model: str = os.getenv("OPENAI_MODEL", getattr(settings, "OPENAI_MODEL", "Qwen3.8-27B-GGUF"))

    timeout_seconds: int = int(os.getenv("AI_TIMEOUT_SECONDS", getattr(settings, "AI_TIMEOUT_SECONDS", 60)))
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", getattr(settings, "ANTHROPIC_API_KEY", "") or "")

ai_config = AIEngineConfig()
