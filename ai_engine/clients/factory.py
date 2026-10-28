from ai_engine.clients.base import BaseLLMClient
from ai_engine.clients.ollama import OllamaClient
from ai_engine.clients.openai_compat import OpenAICompatClient
from ai_engine.config import ai_config

class LLMClientFactory:
    """Factory to get active LLM client based on configuration."""

    @staticmethod
    def get_client() -> BaseLLMClient:
        provider = ai_config.provider.lower()
        if provider in ("openai_compat", "openai","stpi"):
            return OpenAICompatClient()
        elif provider == "ollama":
            return OllamaClient()
        return OpenAICompatClient()