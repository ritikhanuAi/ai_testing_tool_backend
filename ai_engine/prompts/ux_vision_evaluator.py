import json
import base64
from typing import List, Dict, Any, Optional
from ai_engine.clients.factory import LLMClientFactory

VISION_UX_SYSTEM_PROMPT = """You are a senior UX/UI designer and visual quality auditor.
Analyze the provided website screenshot and detect any visual design flaws, usability issues, or layout bugs.

Check specifically for:
1. Text clipping, overflowing outside containers, or overlapping text.
2. Unreadable low-contrast text on banners or buttons.
3. Cramped or poorly spaced interactive elements.
4. Confusing copywriting, ambiguous action buttons, or banner traps blocking content.

Output ONLY valid JSON matching this schema:
[
  {
    "issue_type": "VISUAL_OVERFLOW",
    "title": "Text overflows container boundary",
    "description": "The heading text extends past the card border, making it unreadable.",
    "severity": "MEDIUM",
    "suggested_fix": "Add CSS `word-break: break-word` and ensure card has adequate padding."
  }
]
If the screenshot looks clean with no obvious visual flaws, return an empty list `[]`.
Output raw JSON only.
"""

class UXVisionEvaluator:
    """Uses Qwen-27B VLM on STPI to visually audit website screenshots for layout and UX defects."""

    @classmethod
    async def evaluate_screenshot(
        cls,
        image_bytes: bytes,
        viewport_label: str = "Desktop (1280px)"
    ) -> List[Dict[str, Any]]:
        """
        Sends screenshot to VLM. If the VLM server is offline,
        gracefully returns an empty list without blocking the scan.
        """
        try:
            client = LLMClientFactory.get_client()
            if not await client.is_available():
                return []

            img_b64 = base64.b64encode(image_bytes).decode("utf-8")
            prompt = f"Perform a visual UX and layout review of this {viewport_label} screenshot. Return JSON list of issues."

            if hasattr(client, "generate_with_vision"):
                raw_response = await client.generate_with_vision(
                    prompt=prompt,
                    image_b64=img_b64,
                    system_prompt=VISION_UX_SYSTEM_PROMPT
                )
                cleaned = raw_response.strip()
                if "[" in cleaned and "]" in cleaned:
                    json_str = cleaned[cleaned.find("["):cleaned.rfind("]")+1]
                    data = json.loads(json_str)
                    if isinstance(data, list):
                        return data
        except Exception:
            pass

        return []
