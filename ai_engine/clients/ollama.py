import httpx
from typing import List, Optional
from ai_engine.clients.base import BaseLLMClient
from ai_engine.config import ai_config

class OllamaClient(BaseLLMClient):
    """Local offline Ollama client using async HTTP."""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.base_url = (base_url or ai_config.ollama_base_url).rstrip("/")
        self.model = model or ai_config.ollama_model
        self.vision_model = ai_config.ollama_vision_model
        self.timeout = ai_config.timeout_seconds

    async def is_available(self) -> bool:
        """Check if Ollama server is responding."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                return res.status_code == 200
        except Exception:
            return False

    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Query local Ollama server."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }
        if system_prompt:
            payload["system"] = system_prompt

        async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
            res = await client.post(f"{self.base_url}/api/generate", json=payload)
            res.raise_for_status()
            data = res.json()
            return data.get("response", "")

    async def generate_with_vision(
        self,
        prompt: str,
        image_b64: str | List[str],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send prompt and base64 screenshots to Ollama's local vision model."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        images = image_b64 if isinstance(image_b64, list) else [image_b64]
        messages.append({"role": "user", "content": prompt, "images": images})
        payload = {
            "model": self.vision_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1200},
        }
        async with httpx.AsyncClient(timeout=float(self.timeout)) as client:
            res = await client.post(f"{self.base_url}/api/chat", json=payload)
            res.raise_for_status()
            data = res.json()
            content = data.get("message", {}).get("content", "")
            if not content.strip():
                raise ValueError("Ollama vision model returned an empty response")
            return content.strip()
