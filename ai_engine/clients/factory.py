from ai_engine.clients.base import BaseLLMClient
from ai_engine.clients.ollama import OllamaClient
from ai_engine.config import ai_config

class LLMClientFactory:
    """Factory to get active LLM client based on configuration."""

    @staticmethod
    def get_client() -> BaseLLMClient:
        provider = ai_config.provider.lower()
        if provider == "ollama":
            return OllamaClient()
        # Default fallback to Ollama
        return OllamaClient()
