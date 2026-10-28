import json
from typing import Dict, Any, Optional
from ai_engine.clients.factory import LLMClientFactory

A11Y_FIX_SYSTEM_PROMPT = """You are a senior accessibility engineer and frontend specialist.
For the given WCAG violation, offending element selector, and HTML snippet, provide:
1. Exact, copy-paste ready HTML/CSS code fix.
2. Short 1-sentence developer explanation.

Output ONLY valid JSON matching this schema:
{
  "code_fix": "<button aria-label='Close modal'>&times;</button>",
  "explanation": "Add an aria-label to icon buttons so screen readers can announce their purpose."
}
Output raw JSON only.
"""

DETERMINISTIC_FIXES: Dict[str, str] = {
    "color-contrast": "Ensure text has at least a 4.5:1 contrast ratio against its background. Example: `color: #111827; background-color: #ffffff;`",
    "image-alt": "Add a descriptive `alt` attribute describing the image purpose: `<img src='...' alt='Company headquarters exterior' />`. If decorative, use `alt=''`.",
    "label": "Associate a `<label>` with the input using the `for` attribute: `<label for='email'>Email Address</label><input id='email' name='email' />`.",
    "button-name": "Provide readable text inside `<button>` or add `aria-label='Descriptive action'`.",
    "link-name": "Ensure hyperlinks contain descriptive anchor text instead of 'click here': `<a href='/reports'>View Annual Reports</a>`.",
    "document-title": "Add a unique descriptive title inside `<head>`: `<title>Dashboard — AI Testing Platform</title>`.",
    "html-has-lang": "Declare primary language on the `<html>` root tag: `<html lang='en'>`.",
    "aria-roles": "Verify that ARIA roles, states, and properties conform to WAI-ARIA standards.",
    "heading-order": "Structure headings sequentially without skipping levels (`<h1>` -> `<h2>` -> `<h3>`).",
    "region": "Wrap main page sections in semantic landmark tags: `<header>`, `<nav>`, `<main>`, `<footer>`."
}

class AccessibilityFixer:
    """Generates precise, actionable code fixes for WCAG violations using AI with deterministic fallbacks."""

    @classmethod
    async def get_fix(
        cls,
        rule_id: str,
        help_text: str,
        selector: Optional[str] = None,
        html_snippet: Optional[str] = None
    ) -> str:
        """
        Generates actionable developer fix. If the remote AI model is offline,
        seamlessly returns the exact WCAG compliant fix template.
        """
        # Try AI generation if reachable
        try:
            client = LLMClientFactory.get_client()
            if await client.is_available():
                prompt = f"""WCAG Rule: {rule_id}
Violation: {help_text}
Selector: {selector or 'N/A'}
HTML Snippet: {html_snippet or 'N/A'}

Generate the exact corrected code snippet to fix this violation.
"""
                response = await client.generate(prompt=prompt, system_prompt=A11Y_FIX_SYSTEM_PROMPT)
                cleaned = response.strip()
                if "{" in cleaned and "}" in cleaned:
                    json_str = cleaned[cleaned.find("{"):cleaned.rfind("}")+1]
                    data = json.loads(json_str)
                    fix = data.get("code_fix")
                    expl = data.get("explanation")
                    if fix:
                        return f"{expl} Suggested fix: `{fix}`"
        except Exception:
            pass

        # Robust deterministic fallback
        rule_key = rule_id.lower().strip()
        for key, template in DETERMINISTIC_FIXES.items():
            if key in rule_key:
                if selector:
                    return f"{template} (Target: `{selector}`)"
                return template

        return f"Review WCAG 2.1 AA specifications for rule '{rule_id}' and verify element semantics on selector `{selector or 'DOM node'}`."
