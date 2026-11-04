from ai_engine.clients.base import BaseLLMClient
from ai_engine.clients.ollama import OllamaClient
from ai_engine.clients.remote import RemoteLLMClient
from ai_engine.config import ai_config

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

class LLMClientFactory:
    """Factory to get active LLM client based on configuration."""

    @staticmethod
    def get_client() -> BaseLLMClient:
        provider = ai_config.provider.lower()
        if provider == "ollama":
            return OllamaClient()
        if provider == "gemini":
            return RemoteLLMClient(
                base_url=_GEMINI_BASE_URL,
                api_key=ai_config.gemini_api_key,
                model=ai_config.gemini_model,
            )
        return RemoteLLMClient()
