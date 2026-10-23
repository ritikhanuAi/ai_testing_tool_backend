import json
from playwright.async_api import Page
from typing import Dict, Any

class PageSummaryExtractor:
    """Extracts a compact, token-optimized summary of page forms, buttons, and structure."""

    @staticmethod
    async def extract_summary(page: Page) -> Dict[str, Any]:
        """Extract only interactive elements and semantic landmarks to fit LLM token budgets."""
        summary = await page.evaluate("""
            () => {
                const title = document.title || "";
                
                // Headings
                const headings = Array.from(document.querySelectorAll('h1, h2'))
                    .map(h => h.innerText.trim())
                    .filter(t => t.length > 0)
                    .slice(0, 5);

                // Forms and fields
                const forms = Array.from(document.forms).map((f, fIdx) => {
                    const fields = Array.from(f.querySelectorAll('input, select, textarea')).map(el => {
                        return {
                            tag: el.tagName.toLowerCase(),
                            name: el.name || el.id || "",
                            type: el.type || "text",
                            placeholder: el.placeholder || "",
                            required: el.required || false,
                            selector: el.id ? `#${el.id}` : (el.name ? `[name='${el.name}']` : "")
                        };
                    });
                    return {
                        form_id: f.id || `form_${fIdx}`,
                        action: f.action || "",
                        method: f.method || "GET",
                        fields: fields
                    };
                });

                // Action buttons
                const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], a.btn, a[role="button"]'))
                    .map(b => {
                        const text = (b.innerText || b.value || "").trim();
                        const selector = b.id ? `#${b.id}` : (b.className ? `.${b.className.split(' ')[0]}` : "button");
                        return { text, selector };
                    })
                    .filter(b => b.text.length > 0)
                    .slice(0, 10);

                // Key links
                const links = Array.from(document.querySelectorAll('nav a, header a'))
                    .map(a => ({ text: a.innerText.trim(), href: a.getAttribute('href') || "" }))
                    .filter(a => a.text.length > 0)
                    .slice(0, 10);

                return {
                    title,
                    headings,
                    forms,
                    buttons,
                    navigation_links: links
                };
            }
        """)

        return summary or {
            "title": "",
            "headings": [],
            "forms": [],
            "buttons": [],
            "navigation_links": []
        }
