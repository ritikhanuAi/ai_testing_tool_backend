from abc import ABC, abstractmethod
from typing import Optional

class BaseLLMClient(ABC):
    """Abstract interface for LLM backends."""

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate text completion from prompt."""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if backend service is reachable."""
        pass
