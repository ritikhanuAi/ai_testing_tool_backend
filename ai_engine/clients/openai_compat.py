import httpx 
from typing import Optional, Dict, Any, List
from ai_engine.clients.base import BaseLLMClient

from ai_engine.config import ai_config

class OpenAICompatClient(BaseLLMClient):

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None
    ):
        self.base_url = (base_url or ai_config.open_base_url).rstrip("/")
        self.api_key = (api_key or ai_config.open_api_key)
        self.model = model or ai_config.openai_model
        self.timeout = float(timeout or ai_config.timeout_seconds)

    def _get_headers(self) -> Dict[str, str]:

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    async def is_available(self) -> bool:

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.base_url}/models", headers=self._get_headers())
                return res.status_code in (200, 404, 405)
        except Exception:
            return False
    async def generate(self,prompt: str, system_prompt: Optional[str] = None) -> str:
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content":system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1500,
            "temperature": 0.2
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            endpoint = f"{self.base_url}/chat/completions"
            res = await client.post(endpoint, json=payload, headers=self._get_headers())
            res.raise_for_status()
            data = res.json()
            
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            
            # If Qwen used reasoning tokens, fallback to reasoning if content is empty
            return content.strip() if content.strip() else reasoning.strip()
    async def generate_with_vision(
        self,
        prompt: str,
        image_b64: str | List[str],
        system_prompt: Optional[str] = None
    ) -> str:
        """Send one or more screenshots with a prompt to the configured vision model."""
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        images = image_b64 if isinstance(image_b64, list) else [image_b64]
        user_content = [{"type": "text", "text": prompt}]
        user_content.extend(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}}
            for image in images
        )
        messages.append({"role": "user", "content": user_content})
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1200,
            "temperature": 0.2
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            endpoint = f"{self.base_url}/chat/completions"
            res = await client.post(endpoint, json=payload, headers=self._get_headers())
            res.raise_for_status()
            data = res.json()
            
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            return content.strip() if content.strip() else reasoning.strip()
