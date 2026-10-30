import asyncio
import httpx
from typing import List, Dict, Any, Tuple
from playwright.async_api import Page
from test_engine.pillars.base import PillarFinding
from app.common.constants import FindingSeverity
from app.common.utils import extract_domain

class FunctionalSpider:
    """Discovers interactive forms, inputs, buttons, dead links, and broken images."""

    @staticmethod
    async def extract_forms(page: Page) -> List[Dict[str, Any]]:
        """
        Deeply inspects the live DOM to extract all forms and interactive inputs.
        This provides the structured context the AI needs to generate test cases.
        """
        return await page.evaluate("""
            () => {
                const formsData = [];
                const forms = document.querySelectorAll('form');
                
                forms.forEach((form, fIndex) => {
                    const formId = form.id || `form_${fIndex}`;
                    const formAction = form.getAttribute('action') || '';
                    const formMethod = (form.getAttribute('method') || 'GET').toUpperCase();
                    
                    const inputs = [];
                    const elements = form.querySelectorAll('input, textarea, select');
                    
                    elements.forEach((el, elIndex) => {
                        const type = el.getAttribute('type') || (el.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'text');
                        // Skip hidden and submit inputs from field tests
                        if (type === 'hidden') return;
                        
                        inputs.push({
                            tag: el.tagName.toLowerCase(),
                            type: type,
                            name: el.getAttribute('name') || '',
                            id: el.id || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            required: el.hasAttribute('required'),
                            selector: el.id ? `#${el.id}` : (el.name ? `[name="${el.name}"]` : `input:nth-of-type(${elIndex + 1})`),
                            label: el.labels && el.labels[0] ? el.labels[0].innerText.trim() : ''
                        });
                    });
                    
                    // Locate submit button
                    const submitBtn = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
                    let submitSelector = null;
                    if (submitBtn) {
                        submitSelector = submitBtn.id ? `#${submitBtn.id}` : 
                                         (submitBtn.className ? `.${submitBtn.className.trim().split(/\\s+/)[0]}` : 'button[type="submit"]');
                    }
                    
                    formsData.push({
                        form_index: fIndex,
                        form_id: formId,
                        action: formAction,
                        method: formMethod,
                        inputs: inputs,
                        submit_selector: submitSelector || 'button[type="submit"]'
                    });
                });
                
                return formsData;
            }
        """)

    @staticmethod
    async def audit_links_and_images(page: Page, target_url: str) -> List[PillarFinding]:
        """
        Concurrently checks for dead links (404/500) and broken image sources.
        Uses an asyncio Semaphore to check links in parallel.
        """
        findings: List[PillarFinding] = []
        target_domain = extract_domain(target_url)

        # 1. Extract Links & Candidate Images from DOM
        links = await page.eval_on_selector_all(
            "a[href]",
            "elements => elements.map(el => el.href).filter(href => href.startsWith('http'))"
        )
        candidate_images = await page.evaluate("""
            () => {
                const candidates = [];
                document.querySelectorAll('img').forEach(img => {
                    // Only candidate for check if image explicitly completed loading with 0 dimensions
                    if (img.src && img.complete && img.naturalWidth === 0 && img.naturalHeight === 0) {
                        candidates.push(img.src);
                    }
                });
                return candidates;
            }
        """)

        sem = asyncio.Semaphore(5)

        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            # Verify candidate broken images with HTTP request to eliminate false positives (SVGs / lazy-loading)
            async def check_image(img_src: str):
                async with sem:
                    try:
                        res = await client.head(img_src)
                        if res.status_code in (403, 405):
                            res = await client.get(img_src, headers={"Range": "bytes=0-100"})
                        if res.status_code >= 400:
                            findings.append(PillarFinding(
                                id=f"BROKEN-IMG-{res.status_code}-{abs(hash(img_src)) % 10000}",
                                title=f"Broken image asset detected (HTTP {res.status_code})",
                                description=f"Image asset {img_src} failed to load (HTTP status {res.status_code})",
                                severity=FindingSeverity.HIGH if res.status_code == 404 else FindingSeverity.MEDIUM,
                                selector_or_url=img_src,
                                suggested_fix="Verify image URL path, CDN availability, and asset integrity."
                            ))
                    except Exception as img_err:
                        findings.append(PillarFinding(
                            id=f"BROKEN-IMG-ERR-{abs(hash(img_src)) % 10000}",
                            title="Unreachable image asset detected",
                            description=f"Image {img_src} is unreachable: {str(img_err)}",
                            severity=FindingSeverity.MEDIUM,
                            selector_or_url=img_src,
                            suggested_fix="Verify image URL path, CDN availability, and asset integrity."
                        ))

            if candidate_images:
                unique_images = list(set(candidate_images))[:8]
                await asyncio.gather(*(check_image(img) for img in unique_images))

            # 2. Concurrently verify internal links
            internal_links = [l for l in set(links) if extract_domain(l) == target_domain][:15]
            async def check_link(link: str):
                async with sem:
                    try:
                        res = await client.head(link)
                        if res.status_code >= 400:
                            findings.append(PillarFinding(
                                id=f"BROKEN-LINK-{res.status_code}-{abs(hash(link)) % 10000}",
                                title=f"Broken link detected (HTTP {res.status_code})",
                                description=f"Hyperlink {link} returned HTTP status {res.status_code}",
                                severity=FindingSeverity.HIGH if res.status_code == 404 else FindingSeverity.MEDIUM,
                                selector_or_url=link,
                                suggested_fix="Remove or update dead URL reference."
                            ))
                    except Exception:
                        pass

            await asyncio.gather(*(check_link(l) for l in internal_links))

        return findings
