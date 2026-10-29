import os
from pydantic import BaseModel
from app.core.config import settings

class AIEngineConfig(BaseModel):
    provider: str = os.getenv("AI_PROVIDER", getattr(settings, "AI_PROVIDER", "remote"))
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = os.getenv("OLLAMA_MODEL", getattr(settings, "OLLAMA_MODEL", "qwen2.5:latest"))
    ollama_vision_model: str = os.getenv("OLLAMA_VISION_MODEL", getattr(settings, "OLLAMA_VISION_MODEL", "qwen2.5vl:3b"))

    remote_llm_base_url: str = os.getenv("REMOTE_LLM_BASE_URL", getattr(settings, "REMOTE_LLM_BASE_URL", "https://stpi.roadathena.com/v1"))
    remote_llm_api_key: str = os.getenv("REMOTE_LLM_API_KEY", getattr(settings, "REMOTE_LLM_API_KEY", "hanuai@321"))
    remote_llm_model: str = os.getenv("REMOTE_LLM_MODEL", getattr(settings, "REMOTE_LLM_MODEL", "Qwen3.8-27B-GGUF"))

    timeout_seconds: int = int(os.getenv("AI_TIMEOUT_SECONDS", getattr(settings, "AI_TIMEOUT_SECONDS", 60)))

ai_config = AIEngineConfig()
