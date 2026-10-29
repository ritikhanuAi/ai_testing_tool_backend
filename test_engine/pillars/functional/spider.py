import asyncio
import hashlib
import httpx
from typing import List, Dict, Any
from urllib.parse import urlsplit
from playwright.async_api import Page
from test_engine.pillars.base import PillarFinding
from app.common.constants import FindingSeverity
from app.common.utils import extract_domain
from test_engine.policy.allowlist import TargetAllowlistValidator
from test_engine.crawl.site_crawler import redact_url

class FunctionalSpider:
    """Discovers interactive forms, inputs, buttons, dead links, and broken images."""

    @staticmethod
    async def extract_interactive_inventory(page: Page) -> Dict[str, Any]:
        """Return visible controls and their exact, reusable locators, including form-free widgets."""
        return await page.evaluate(r"""
            () => {
                const forms = Array.from(document.forms);
                const formId = form => {
                    const index = forms.indexOf(form);
                    return form.id || `form_${index}`;
                };
                const roleFor = el => {
                    const explicit = el.getAttribute('role');
                    if (explicit) return explicit.trim().split(/\s+/)[0];
                    const tag = el.tagName.toLowerCase();
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    if (/^h[1-6]$/.test(tag)) return 'heading';
                    if (tag === 'button' || (tag === 'input' && ['button', 'submit', 'reset', 'image'].includes(type))) return 'button';
                    if (tag === 'a' && el.hasAttribute('href')) return 'link';
                    if (tag === 'select') return 'combobox';
                    if (tag === 'textarea' || el.isContentEditable) return 'textbox';
                    if (tag === 'input') {
                        if (type === 'checkbox') return 'checkbox';
                        if (type === 'radio') return 'radio';
                        if (type === 'range') return 'slider';
                        if (type === 'search') return 'searchbox';
                        if (['hidden', 'file', 'color'].includes(type)) return null;
                        return 'textbox';
                    }
                    if (el.hasAttribute('onclick') || (el.tabIndex >= 0 && el.getAttribute('aria-label'))) return 'button';
                    return null;
                };
                const accessibleName = el => {
                    const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\s+/)
                        .map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
                    const label = el.labels ? Array.from(el.labels).map(item => item.innerText).join(' ').trim() : '';
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    const buttonValue = el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(type) ? el.value : '';
                    return (el.getAttribute('aria-label') || labelledBy || label || el.getAttribute('placeholder') ||
                        el.getAttribute('title') || el.innerText || buttonValue || '').trim().replace(/\s+/g, ' ').slice(0, 160);
                };
                const cssPath = el => {
                    const parts = [];
                    let node = el;
                    while (node && node.nodeType === Node.ELEMENT_NODE) {
                        if (node.hasAttribute('data-testid')) {
                            const testId = `[data-testid=${JSON.stringify(node.getAttribute('data-testid'))}]`;
                            if (document.querySelectorAll(testId).length === 1) return testId;
                        }
                        if (node.hasAttribute('name')) {
                            const named = `${node.localName}[name=${JSON.stringify(node.getAttribute('name'))}]`;
                            if (document.querySelectorAll(named).length === 1) return named;
                        }
                        if (node.id) {
                            parts.unshift(`#${CSS.escape(node.id)}`);
                            const candidate = parts.join(' > ');
                            if (document.querySelectorAll(candidate).length === 1) return candidate;
                        }
                        const tag = node.localName;
                        const siblings = node.parentElement ? Array.from(node.parentElement.children).filter(item => item.localName === tag) : [node];
                        const suffix = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(node) + 1})` : '';
                        parts.unshift(`${tag}${suffix}`);
                        const candidate = parts.join(' > ');
                        try {
                            if (document.querySelectorAll(candidate).length === 1) return candidate;
                        } catch (_) {}
                        node = node.parentElement;
                    }
                    return parts.join(' > ');
                };
                const candidates = Array.from(document.querySelectorAll(
                    'button, input, select, textarea, a[href], summary, h1, h2, h3, h4, h5, h6, [role], [contenteditable="true"], [tabindex], [aria-label], [onclick]'
                ));
                const elements = candidates.filter(el => {
                    const style = window.getComputedStyle(el);
                    return roleFor(el) && !el.disabled && !el.hidden && el.getAttribute('aria-disabled') !== 'true' &&
                        el.getAttribute('aria-hidden') !== 'true' &&
                        style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
                }).slice(0, 60).map(el => {
                    const tag = el.tagName.toLowerCase();
                    const attributeType = (el.getAttribute('type') || '').toLowerCase();
                    const type = tag === 'input' ? (attributeType || 'text') :
                        tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : attributeType;
                    const role = roleFor(el);
                    const name = accessibleName(el);
                    const credentialControl = type === 'password' ||
                        /\b(password|passcode|username|user name|log ?in|one.time.code|verification code)\b/.test(name.toLowerCase()) ||
                        /(^|\s)(current-password|new-password|username|one-time-code)(\s|$)/.test(el.getAttribute('autocomplete') || '');
                    const selector = cssPath(el);
                    const form = el.closest('form') || el.form;
                    const isSubmit = tag === 'button' ? (attributeType === 'submit' || (!attributeType && !!form)) :
                        tag === 'input' && ['submit', 'image'].includes(type);
                    const lowerName = name.toLowerCase();
                    const riskyName = /\b(submit|save|delete|remove|pay|purchase|checkout|send|create|update|register|sign ?up|log ?out|confirm|buy|order|post|publish|upload|book|reserve|transfer|unsubscribe)\b/.test(lowerName);
                    const allowedActions = [];
                    if (['textbox', 'searchbox'].includes(role) && !credentialControl) allowedActions.push('type_text');
                    if (role === 'combobox' && name && !form) allowedActions.push(tag === 'select' ? 'select_option' : 'click');
                    if (['checkbox', 'radio'].includes(role) && name && !form && !riskyName) allowedActions.push('click');
                    if (['tab', 'menuitem'].includes(role) && name && !riskyName) allowedActions.push('click');
                    if (role === 'button' && name && !isSubmit && !form && !riskyName) allowedActions.push('click');
                    if (['button', 'link', 'tab', 'menuitem'].includes(role)) allowedActions.push('hover');
                    return {
                        selector,
                        fallback_role: name ? role : null,
                        fallback_name: name || null,
                        tag,
                        type,
                        role,
                        name,
                        placeholder: el.getAttribute('placeholder') || '',
                        required: !!el.required || el.getAttribute('aria-required') === 'true',
                        form_id: form ? formId(form) : null,
                        is_submit: isSubmit,
                        allowed_actions: allowedActions,
                        options: tag === 'select' ? Array.from(el.options).map(option => ({label: option.label, value: option.value})).slice(0, 20) : []
                    };
                });
                const formsData = forms.map((form, index) => {
                    const id = formId(form);
                    const controls = elements.filter(item => item.form_id === id);
                    const submit = controls.find(item => item.is_submit);
                    return {
                        form_index: index,
                        form_id: id,
                        action: form.getAttribute('action') || '',
                        method: (form.getAttribute('method') || 'GET').toUpperCase(),
                        selector: cssPath(form),
                        inputs: controls.filter(item => ['textbox', 'searchbox', 'combobox', 'checkbox', 'radio'].includes(item.role) && !item.is_submit).map(item => ({
                            tag: item.tag, type: item.type || item.role, name: item.name, placeholder: item.placeholder,
                            required: item.required, selector: item.selector, fallback_role: item.fallback_role,
                            fallback_name: item.fallback_name, role: item.role, options: item.options
                        })),
                        submit_selector: submit ? submit.selector : null,
                        submit_role: submit ? submit.fallback_role : null,
                        submit_name: submit ? submit.fallback_name : null
                    };
                });
                return {elements, forms: formsData};
            }
        """)

    @classmethod
    async def extract_forms(cls, page: Page) -> List[Dict[str, Any]]:
        return (await cls.extract_interactive_inventory(page)).get("forms", [])

    @classmethod
    async def extract_interactive_catalog(cls, page: Page) -> List[Dict[str, Any]]:
        return (await cls.extract_interactive_inventory(page)).get("elements", [])

    @staticmethod
    async def audit_links_and_images(
        page: Page,
        target_url: str,
        *,
        check_internal_links: bool = True,
    ) -> List[PillarFinding]:
        """
        Concurrently checks for dead links (404/500) and broken image sources.
        Uses an asyncio Semaphore to check links in parallel.
        """
        findings: List[PillarFinding] = []
        target_domain = extract_domain(target_url)

        # 1. Extract Links & Images from DOM
        links = await page.eval_on_selector_all(
            "a[href]",
            "elements => elements.map(el => el.href).filter(href => href.startsWith('http'))"
        )
        broken_images = await page.evaluate("""
            () => {
                const broken = [];
                document.querySelectorAll('img').forEach(img => {
                    if (img.src && (!img.complete || img.naturalWidth === 0)) {
                        broken.push(img.src);
                    }
                });
                return broken;
            }
        """)

        # Add broken image findings
        for img_src in list(set(broken_images))[:5]:
            findings.append(PillarFinding(
                id=f"BROKEN-IMG-{hashlib.sha1(redact_url(img_src).encode()).hexdigest()[:12]}",
                title="Broken image asset detected",
                description=f"Image failed to render (zero natural width or load error): {redact_url(img_src)}",
                severity=FindingSeverity.MEDIUM,
                selector_or_url=redact_url(img_src),
                suggested_fix="Verify image URL path, CDN availability, and asset integrity."
            ))

        # 2. Concurrently verify internal links
        internal_links = [l for l in set(links) if extract_domain(l) == target_domain][:15] if check_internal_links else []
        sem = asyncio.Semaphore(5)

        async with httpx.AsyncClient(timeout=6.0, follow_redirects=False, trust_env=False) as client:
            async def check_link(link: str):
                async with sem:
                    try:
                        hostname = urlsplit(link).hostname or ""
                        is_public, _ = await asyncio.to_thread(
                            TargetAllowlistValidator.assert_public_host, hostname
                        )
                        if not is_public:
                            return
                        res = await client.head(link)
                        if res.status_code >= 400:
                            findings.append(PillarFinding(
                                id=f"BROKEN-LINK-{res.status_code}-{hashlib.sha1(link.encode()).hexdigest()[:12]}",
                                title=f"Broken link detected (HTTP {res.status_code})",
                                description=f"Hyperlink {redact_url(link)} returned HTTP status {res.status_code}",
                                severity=FindingSeverity.HIGH if res.status_code == 404 else FindingSeverity.MEDIUM,
                                selector_or_url=redact_url(link),
                                suggested_fix="Remove or update dead URL reference."
                            ))
                    except Exception:
                        pass

            await asyncio.gather(*(check_link(l) for l in internal_links))

        return findings
