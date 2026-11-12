"""
Ephemeral Playwright session: login → DOM nav extraction → batched AI goal generation.
Uses async_playwright directly (NOT BrowserPool) — this is a synchronous request-scoped
operation, not a long-lived Celery worker process.
"""
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse

from app.apps.test_runs.serializers import (
    DiscoverModulesRequest,
    DiscoveredModule,
    DiscoverModulesResponse,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JavaScript executed in the browser to extract navigation items from the DOM.
# Filters out non-testable links (logout, delete, external, file downloads).
# ---------------------------------------------------------------------------
_NAV_EXTRACTION_JS = """() => {
    const seen = new Set();
    const results = [];
    const pageHost = location.hostname;

    const SKIP_PATH_KEYWORDS = [
        'logout', 'log-out', 'signout', 'sign-out', 'logoff', 'log-off',
        'delete', 'remove', 'destroy', 'unsubscribe', 'deactivate',
    ];
    const SKIP_EXTENSIONS = ['.pdf', '.zip', '.xlsx', '.xls', '.doc', '.docx', '.csv', '.exe', '.dmg'];

    function shouldSkip(href, text) {
        if (!href || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:') || href === '#') return true;
        try {
            const u = new URL(href, location.href);
            if (u.hostname && u.hostname !== pageHost) return true;
            const path = u.pathname.toLowerCase();
            if (SKIP_EXTENSIONS.some(ext => path.endsWith(ext))) return true;
            if (SKIP_PATH_KEYWORDS.some(kw => path.includes(kw))) return true;
        } catch(e) { return true; }
        const lowerText = (text || '').toLowerCase();
        if (SKIP_PATH_KEYWORDS.some(kw => lowerText.includes(kw))) return true;
        return false;
    }

    function getLinkText(a) {
        const direct = (a.innerText || a.textContent || '').trim();
        if (direct) return direct;
        const label = a.getAttribute('aria-label') || a.getAttribute('title') || '';
        if (label) return label.trim();
        const img = a.querySelector('img[alt]');
        if (img) return img.alt.trim();
        return '';
    }

    function addItem(name, href, depth) {
        try {
            const u = new URL(href, location.href);
            const normalized = u.origin + u.pathname;
            if (seen.has(normalized)) return;
            const cleanName = (name || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
            if (!cleanName) return;
            seen.add(normalized);
            results.push({ name: cleanName, url: href, depth });
        } catch(e) {}
    }

    // Priority 1: semantic nav + role=navigation + menubar
    const navContainers = [];
    document.querySelectorAll('nav, [role="navigation"], [role="menubar"]').forEach(el => navContainers.push(el));

    // Priority 2: sidebar / drawer / menu class patterns
    const sidebarSelectors = [
        '.sidebar', '.side-nav', '.sidenav', '.left-nav', '.left-sidebar',
        '.menu', '.main-menu', '.app-menu', '.app-nav', '.nav-menu',
        '[class*="sidebar"]', '[class*="side-nav"]', '[class*="sidenav"]',
        '[class*="left-nav"]', '[class*="leftNav"]',
        '[class*="menu"]', '[class*="Menu"]',
        '[class*="drawer"]', '[class*="Drawer"]',
        '[id*="sidebar"]', '[id*="side-nav"]', '[id*="menu"]',
        'aside', '[role="complementary"]',
    ];
    sidebarSelectors.forEach(sel => {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (!navContainers.includes(el)) navContainers.push(el);
            });
        } catch(e) {}
    });

    navContainers.forEach((nav, navIdx) => {
        nav.querySelectorAll('a[href]').forEach(a => {
            const href = a.href;
            const text = getLinkText(a);
            if (!shouldSkip(href, text)) {
                addItem(text, href, navIdx === 0 ? 0 : 1);
            }
        });
    });

    // Priority 3: header / topbar
    if (results.length < 8) {
        document.querySelectorAll('header a[href], [role="banner"] a[href], [class*="header"] a[href], [class*="topbar"] a[href], [class*="toolbar"] a[href]').forEach(a => {
            const text = getLinkText(a);
            if (!shouldSkip(a.href, text)) addItem(text, a.href, 0);
        });
    }

    // Priority 4: any ul/li list links (common pattern for nav menus)
    if (results.length < 8) {
        document.querySelectorAll('ul li a[href], ol li a[href]').forEach(a => {
            const text = getLinkText(a);
            if (!shouldSkip(a.href, text)) addItem(text, a.href, 2);
        });
    }

    // Priority 5: FULL PAGE SCAN — always run to catch any missed internal links
    // Also maps group header names to their first child link URL
    {
        const pathSeen = new Set(Array.from(seen).map(u => { try { return new URL(u).pathname; } catch(e) { return u; } }));
        // Collect group headers with their first child link
        const nav = document.querySelector('nav, aside, [role="navigation"]');
        if (nav) {
            nav.querySelectorAll('div[style*="cursor: pointer"], li[style*="cursor: pointer"]').forEach(div => {
                const span = div.querySelector('span');
                const groupName = span ? span.innerText.trim() : (div.innerText||'').trim().split('\\n')[0].trim();
                if (!groupName || groupName.length < 2 || groupName.length > 40) return;
                if (SKIP_PATH_KEYWORDS.some(kw => groupName.toLowerCase().includes(kw))) return;
                const sib = div.nextElementSibling;
                if (!sib) return;
                const firstLink = sib.querySelector('a[href]');
                if (firstLink && !shouldSkip(firstLink.href, groupName)) {
                    const path = new URL(firstLink.href).pathname;
                    if (!pathSeen.has(path)) {
                        pathSeen.add(path);
                        addItem(groupName, firstLink.href, 1);
                    }
                }
            });
        }
        // Also scan all page links
        document.querySelectorAll('a[href]').forEach(a => {
            try {
                const u = new URL(a.href, location.href);
                if (u.hostname !== pageHost) return;
                const path = u.pathname;
                if (pathSeen.has(path)) return;
                const text = getLinkText(a);
                if (!shouldSkip(a.href, text) && text.length > 1) {
                    pathSeen.add(path);
                    addItem(text, a.href, 3);
                }
            } catch(e) {}
        });
    }

    return results.slice(0, 50);
}"""


async def _login_for_discovery(page, credentials) -> bool:
    """
    Robust login for module discovery.
    Returns True if URL changed after submit (login likely succeeded).
    """
    login_url = credentials.login_url or page.url
    username = credentials.username or ""
    password = credentials.password or ""

    try:
        current_url = page.url
        if login_url and login_url.rstrip("/") != current_url.rstrip("/"):
            await page.goto(login_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(1000)

        # Fill username — try progressively broader selectors
        username_selectors = [
            'input[type="email"]',
            'input[name="username"]',
            'input[name="user"]',
            'input[id="username"]',
            'input[name*="email"]',
            'input[id*="email"]',
            'input[id*="user"]',
            'input[type="text"]',  # generic text fallback
        ]
        filled_user = False
        for sel in username_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.fill(username)
                    filled_user = True
                    logger.info("Module discovery login: filled username with selector %r", sel)
                    break
            except Exception:
                continue

        # Fill password
        filled_pass = False
        try:
            pass_loc = page.locator('input[type="password"]').first
            if await pass_loc.count() > 0:
                await pass_loc.fill(password)
                filled_pass = True
        except Exception:
            pass

        if not filled_user or not filled_pass:
            logger.warning(
                "Module discovery login: could not fill fields (user=%s pass=%s)",
                filled_user, filled_pass
            )

        before_url = page.url

        # Click submit — try multiple strategies
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign in")',
            'button:has-text("Log in")',
            'button:has-text("Submit")',
        ]
        clicked = False
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click()
                    clicked = True
                    logger.info("Module discovery login: clicked submit with selector %r", sel)
                    break
            except Exception:
                continue

        if not clicked:
            logger.warning("Module discovery login: no submit button found, trying Enter key")
            try:
                await page.keyboard.press("Enter")
                clicked = True
            except Exception:
                pass

        if clicked:
            # Wait for URL to change — up to 10s
            try:
                await page.wait_for_url(
                    lambda url: url.rstrip("/") != before_url.rstrip("/"),
                    timeout=10000,
                )
                logger.info("Module discovery login: URL changed to %s", page.url)
            except Exception:
                logger.warning("Module discovery login: URL did not change after submit (still %s)", page.url)

        # Extra wait for SPA to settle
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        success = page.url.rstrip("/") != before_url.rstrip("/")
        logger.info("Module discovery login: success=%s final_url=%s", success, page.url)
        return success

    except Exception as e:
        logger.warning("Module discovery login failed: %s", e)
        return False


def _infer_name_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    if not path or path == "/":
        return "Home"
    segment = path.split("/")[-1]
    return segment.replace("-", " ").replace("_", " ").title() or "Page"


async def _looks_like_login_page(page) -> bool:
    """
    A page is treated as a login page whenever it shows a visible password
    input. URL path is deliberately ignored — many SPAs host the login form
    at /, /welcome, or a non-obvious route.
    """
    try:
        loc = page.locator('input[type="password"]').first
        if await loc.count() == 0:
            return False
        return bool(await loc.is_visible())
    except Exception:
        return False


_DETAIL_ID_SEGMENT = re.compile(
    r"/(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$",
    re.IGNORECASE,
)


def _collapse_detail_duplicates(modules: list[dict]) -> list[dict]:
    """
    Collapse repetitive detail-page entries like 20 rows of "View" at
    /jobs/39/, /jobs/40/, /jobs/41/, ... — common pattern on list pages
    where each table row has an identically-named link to a different record.

    Rule: when 3+ modules share the same (lowercase name, parent path), keep the
    first as a representative detail page (renamed to show it's a sample) and
    drop the rest. If the parent list page isn't already a module, add it.
    """
    from collections import defaultdict
    from urllib.parse import urlparse, urlunparse

    def parent_of(url: str) -> Optional[str]:
        path = urlparse(url).path
        stripped = _DETAIL_ID_SEGMENT.sub("/", path)
        if stripped == path or not stripped or stripped == "/":
            return None
        return stripped

    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for idx, m in enumerate(modules):
        name = (m.get("name") or "").strip()
        if not name:
            continue
        parent = parent_of(m["url"])
        if parent:
            groups[(name.lower(), parent)].append(idx)

    existing_paths = {urlparse(m["url"]).path.rstrip("/") for m in modules}

    to_drop: set[int] = set()
    replacements: dict[int, dict] = {}
    to_add: list[dict] = []
    collapsed_groups = 0

    for (_name_lc, parent_path), indices in groups.items():
        if len(indices) < 3:
            continue
        collapsed_groups += 1
        to_drop.update(indices[1:])

        parent_key = parent_path.rstrip("/")
        parent_segment = parent_key.rsplit("/", 1)[-1] if parent_key else ""
        parent_label = (
            parent_segment.replace("-", " ").replace("_", " ").title()
            if parent_segment
            else ""
        )

        rep = dict(modules[indices[0]])
        orig_name = rep["name"]
        if parent_label and parent_label.lower() not in orig_name.lower():
            rep["name"] = f"{parent_label}: {orig_name} (1 of {len(indices)})"
        else:
            rep["name"] = f"{orig_name} (1 of {len(indices)})"
        replacements[indices[0]] = rep

        if parent_key and parent_key not in existing_paths:
            parsed = urlparse(modules[indices[0]]["url"])
            parent_url = urlunparse(parsed._replace(path=parent_path, query="", fragment=""))
            to_add.append({
                "name": parent_label or _infer_name_from_url(parent_url),
                "url": parent_url,
                "depth": max(0, rep.get("depth", 1) - 1),
            })
            existing_paths.add(parent_key)

    if collapsed_groups:
        logger.info(
            "Module discovery: collapsed %d detail-duplicate group(s), removed %d entries",
            collapsed_groups,
            len(to_drop),
        )

    result: list[dict] = []
    for i, m in enumerate(modules):
        if i in to_drop:
            continue
        result.append(replacements.get(i, m))
    result.extend(to_add)
    return result


async def _generate_goals_for_modules(modules: list[dict]) -> dict[str, str]:
    """ONE batched LLM call to generate test goals for all discovered modules."""
    if not modules:
        return {}

    import asyncio
    from ai_engine.clients.factory import LLMClientFactory

    module_list = "\n".join(
        f'{i+1}. Name: "{m["name"]}" | URL: {m["url"]}'
        for i, m in enumerate(modules)
    )

    system_prompt = (
        "You are a QA planning expert. For each website module, write one concise test goal "
        "(12-20 words) describing the most important user workflow to verify. "
        "Return ONLY a valid JSON array. Each element must have exactly two keys: "
        '"url" (exact URL from input) and "goal" (the test goal string). '
        "No explanation, no markdown, no code fences."
    )

    user_prompt = f"""Generate one test goal per module listed below.

<modules>
{module_list}
</modules>

Return JSON array: [{{"url": "...", "goal": "..."}}]"""

    try:
        client = LLMClientFactory.get_client()
        raw = await asyncio.wait_for(
            client.generate(prompt=user_prompt, system_prompt=system_prompt),
            timeout=25,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:])
        if cleaned.endswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[:-1])
        cleaned = cleaned.strip()
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return {item["url"]: item["goal"] for item in parsed if "url" in item and "goal" in item}
    except Exception as e:
        logger.warning("Batched goal generation failed: %s", e)

    return {
        m["url"]: f"Verify {m['name']} page loads and core interactions function correctly."
        for m in modules
    }


async def _click_discover_nav(page, home_url: str, already_seen: set[str]) -> list[dict]:
    """
    Click-based nav discovery for SPAs that use JavaScript routing without real href.
    Finds all clickable items inside nav containers, clicks each, records URL changes.
    """
    SKIP_TEXT = {'logout', 'log out', 'sign out', 'signout', 'log-out',
                 'delete', 'remove', 'deactivate', 'unsubscribe', 'profile', 'settings',
                 'help', 'support', 'account', 'notifications'}

    # Collect candidate nav item texts — strict filtering to avoid chart labels / tooltips
    candidates: list[dict] = await page.evaluate("""() => {
        const results = [];
        const seen = new Set();
        const SKIP_TEXT = ['logout','log out','sign out','signout','delete','remove',
                           'deactivate','unsubscribe','profile','notifications','help',
                           'support','account'];

        // Only look inside dedicated nav/sidebar containers — NOT main/article/section
        const NAV_SELECTORS = [
            'nav', 'aside',
            '[role="navigation"]', '[role="menu"]', '[role="menubar"]',
            '[class*="sidebar"]', '[class*="Sidebar"]',
            '[class*="sideNav"]', '[class*="SideNav"]',
            '[class*="leftNav"]', '[class*="LeftNav"]',
            '[class*="drawer"]', '[class*="Drawer"]',
        ];

        // Collect nav containers but EXCLUDE main content areas
        const EXCLUDE_SELECTORS = ['main', 'article', 'section', '[role="main"]',
                                    '[class*="content"]', '[class*="dashboard"]',
                                    '[class*="chart"]', '[class*="table"]', '[class*="grid"]'];

        const containers = [];
        NAV_SELECTORS.forEach(sel => {
            try {
                document.querySelectorAll(sel).forEach(el => {
                    // Skip if it's inside a main content area
                    const insideMain = EXCLUDE_SELECTORS.some(ex => el.closest(ex));
                    if (!insideMain && !containers.includes(el)) containers.push(el);
                });
            } catch(e) {}
        });

        containers.forEach(container => {
            // Only look at SHALLOW children (max 4 levels) to avoid chart descendants
            const walker = document.createTreeWalker(container, NodeFilter.SHOW_ELEMENT, {
                acceptNode(node) {
                    // Count depth from container
                    let depth = 0, cur = node;
                    while (cur && cur !== container) { cur = cur.parentElement; depth++; }
                    if (depth > 4) return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }
            });

            const visited = [];
            let node = walker.nextNode();
            while (node) { visited.push(node); node = walker.nextNode(); }

            visited.forEach(el => {
                // Skip if has child that is also a link-like element (avoid parent)
                const hasChildLink = el.querySelector('a, [role="menuitem"], button');
                if (hasChildLink) return;

                const style = window.getComputedStyle(el);
                const isCursorPointer = style.cursor === 'pointer';
                const hasNavRole = ['menuitem','option','button','link'].includes(el.getAttribute('role') || '');
                const isButton = el.tagName === 'BUTTON';
                if (!isCursorPointer && !hasNavRole && !isButton) return;

                const raw = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                // Strict text requirements: short (2–30 chars), mostly letters, no % / : numbers
                if (!raw || raw.length < 2 || raw.length > 30) return;
                // Skip if contains digits followed by % or contains ':' (stats/labels)
                if (/\\d+[%:(]/.test(raw) || /^\\d/.test(raw)) return;
                // Skip if more than 1 special character (chart labels tend to have them)
                if ((raw.match(/[^a-zA-Z0-9 \\-_]/g) || []).length > 1) return;
                if (SKIP_TEXT.some(s => raw.toLowerCase().includes(s))) return;
                if (seen.has(raw)) return;

                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;

                seen.add(raw);
                results.push({ text: raw, tag: el.tagName.toLowerCase() });
            });
        });

        return results.slice(0, 20);
    }""")

    logger.info("Click discovery: found %d candidate nav items", len(candidates))

    discovered: list[dict] = []

    for item in candidates:
        text = item["text"]
        if any(skip in text.lower() for skip in SKIP_TEXT):
            continue

        before_url = page.url
        try:
            # Try role=menuitem first, then exact text, then contains text
            el = None
            for loc in [
                page.get_by_role("menuitem", name=text),
                page.get_by_role("option", name=text),
                page.locator(f'[role="button"]:has-text("{text}")').first,
                page.locator(f'button:has-text("{text}")').first,
                page.get_by_text(text, exact=True).first,
            ]:
                try:
                    if await loc.count() > 0 and await loc.first.is_visible():
                        el = loc.first
                        break
                except Exception:
                    continue

            if el is None:
                continue

            await el.click(timeout=2500)

            # Wait up to 2s for URL to change
            try:
                await page.wait_for_url(
                    lambda url: url.rstrip("/") != before_url.rstrip("/"),
                    timeout=2000,
                )
            except Exception:
                pass

            after_url = page.url
            after_clean = after_url.rstrip("/")
            before_clean = before_url.rstrip("/")

            if after_clean != before_clean and after_url not in already_seen:
                already_seen.add(after_url)
                discovered.append({"name": text, "url": after_url, "depth": 1})
                logger.info("Click discovery: %r → %s", text, after_url)

        except Exception as exc:
            logger.debug("Click discovery: failed for %r: %s", text, exc)

        # Always navigate back to home for next click
        try:
            if page.url.rstrip("/") != home_url.rstrip("/"):
                await page.goto(home_url, wait_until="domcontentloaded", timeout=12000)
                await page.wait_for_timeout(800)
        except Exception:
            pass

    return discovered


async def discover_site_modules(request_data: DiscoverModulesRequest) -> DiscoverModulesResponse:
    """
    Launch ephemeral Playwright browser, login if credentials supplied,
    extract navigation modules from DOM, generate AI test goals, return module list.
    """
    from playwright.async_api import async_playwright
    from test_engine.crawl.site_crawler import SiteCrawler

    start = time.perf_counter()
    target_url = request_data.url.strip()
    parsed = urlparse(target_url)
    hostname = parsed.hostname or ""
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    root_port = parsed.port
    allowed_domains = {hostname}
    if hostname.count(".") >= 1:
        allowed_domains.add(".".join(hostname.split(".")[-2:]))

    playwright = None
    browser = None
    context = None
    page = None

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            ignore_https_errors=False,
        )
        page = await context.new_page()

        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

        auth_requested = bool(request_data.credentials and request_data.credentials.enabled)
        if auth_requested:
            await _login_for_discovery(page, request_data.credentials)

        # Wait for the page to settle after login (SPA sidebar renders after JS hydration)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        # Recovery: SPAs often briefly navigate to a protected route after login,
        # then an async auth check fails and bounces back to /login. Detect this
        # and try one re-navigation to the user-supplied entry URL.
        if auth_requested and await _looks_like_login_page(page):
            logger.warning(
                "Module discovery: still on login-like page after auth (%s); "
                "retrying by navigating to %s",
                page.url, target_url,
            )
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
            except Exception as e:
                logger.warning("Module discovery: recovery navigation failed: %s", e)

            if await _looks_like_login_page(page):
                logger.warning(
                    "Module discovery: still on login page after recovery (%s). "
                    "Credentials may be wrong or the SPA requires extra auth steps — "
                    "module list will reflect only what the login page exposes.",
                    page.url,
                )

        post_login_url = page.url
        logger.info("Module discovery: post-login URL = %s", post_login_url)

        # Re-build allowed_domains from the post-login URL
        post_parsed = urlparse(post_login_url)
        post_hostname = post_parsed.hostname or hostname
        if post_parsed.port:
            root_port = post_parsed.port
        allowed_domains.add(post_hostname)
        if post_hostname.count(".") >= 1:
            allowed_domains.add(".".join(post_hostname.split(".")[-2:]))
        if hostname.count(".") >= 1:
            allowed_domains.add(f"*.{'.'.join(hostname.split('.')[-2:])}")

        # --- Primary strategy: expand each collapsible group header one-by-one ---
        # Links are lazy-rendered (not in DOM until group is clicked open).
        deduped: list[dict] = []
        seen_urls: set[str] = set()

        SKIP_GROUP_KEYWORDS = {'logout', 'sign out', 'signout', 'delete', 'remove', 'deactivate'}

        group_count = await page.locator('nav div[style*="cursor: pointer"]').count()
        logger.info("Module discovery: found %d sidebar group headers", group_count)

        for i in range(group_count):
            header = page.locator('nav div[style*="cursor: pointer"]').nth(i)
            try:
                group_name_raw = await header.locator('span').first.inner_text(timeout=1000)
                group_name = group_name_raw.strip()
            except Exception:
                group_name = f"Group {i+1}"

            if any(kw in group_name.lower() for kw in SKIP_GROUP_KEYWORDS):
                continue

            # Check if already expanded (maxHeight set to large value)
            max_h = await header.evaluate("el => el.nextElementSibling ? el.nextElementSibling.style.maxHeight : ''")
            if max_h not in ('', '0px'):
                # Already open — just collect links
                pass
            else:
                # Click to expand and wait for React to render child links
                try:
                    await header.click(timeout=3000)
                    await page.wait_for_timeout(400)
                except Exception as e:
                    logger.debug("Could not click group %r: %s", group_name, e)
                    continue

            links = await header.evaluate("""el => {
                const sib = el.nextElementSibling;
                if (!sib) return [];
                return Array.from(sib.querySelectorAll('a[href]')).map(a => ({
                    href: a.href,
                    text: (a.innerText || a.textContent || '').replace(/\\s+/g, ' ').trim()
                }));
            }""")

            for link in links:
                href = link.get("href", "")
                raw_text = link.get("text", "").strip()
                if not href or not raw_text:
                    continue
                try:
                    normalized, classification = SiteCrawler._normalize_link(
                        href, post_login_url, allowed_domains, root_port
                    )
                except Exception:
                    continue
                if classification != "ok" or not normalized:
                    continue
                if normalized in seen_urls:
                    continue
                seen_urls.add(normalized)
                # Use "Group > SubItem" naming so the user sees the context
                display_name = f"{group_name.title()} — {raw_text}" if group_name and raw_text != group_name else raw_text
                deduped.append({"name": display_name, "url": normalized, "depth": 1})
                logger.debug("  module: %r -> %s", display_name, normalized)

            # Collapse again to keep sidebar manageable (optional — skip if slow)
            if max_h in ('', '0px'):
                try:
                    await header.click(timeout=2000)
                    await page.wait_for_timeout(150)
                except Exception:
                    pass

        logger.info("Module discovery: group expansion found %d modules", len(deduped))

        # --- Fallback: href scan for apps without collapsible groups ---
        if len(deduped) < 3:
            logger.info("Module discovery: group expansion found too few — falling back to href scan")
            modules_raw: list[dict] = await page.evaluate(_NAV_EXTRACTION_JS)
            logger.info("Module discovery: JS extracted %d raw links", len(modules_raw))
            for item in modules_raw:
                href = item.get("url", "")
                if not href:
                    continue
                try:
                    normalized, classification = SiteCrawler._normalize_link(
                        href, post_login_url, allowed_domains, root_port
                    )
                except Exception:
                    continue
                if classification == "ok" and normalized and normalized not in seen_urls:
                    seen_urls.add(normalized)
                    deduped.append({"name": item.get("name", "").strip(), "url": normalized, "depth": item.get("depth", 0)})

        if not deduped:
            deduped = [{"name": "Home", "url": post_login_url, "depth": 0}]

        # --- Interconnected module discovery: follow each top-level module and extract sub-pages ---
        # Only do this when we found a reasonable set of top-level modules (to avoid slow scans on trivial sites)
        if len(deduped) >= 2:
            top_level_urls = [m["url"] for m in deduped[:12]]  # cap to avoid too many requests
            for module_url in top_level_urls:
                try:
                    await page.goto(module_url, wait_until="domcontentloaded", timeout=15000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    sub_raw: list[dict] = await page.evaluate(_NAV_EXTRACTION_JS)
                    for item in sub_raw:
                        href = item.get("url", "")
                        if not href:
                            continue
                        try:
                            normalized, classification = SiteCrawler._normalize_link(
                                href, module_url, allowed_domains, root_port
                            )
                        except Exception:
                            continue
                        if classification == "ok" and normalized and normalized not in seen_urls:
                            seen_urls.add(normalized)
                            name = item.get("name", "").strip()
                            deduped.append({"name": name, "url": normalized, "depth": 2})
                            logger.debug("  sub-module: %r -> %s (from %s)", name, normalized, module_url)
                except Exception as e:
                    logger.debug("Sub-module discovery failed for %s: %s", module_url, e)

            logger.info("Module discovery: after sub-page scan, total modules = %d", len(deduped))

        # Collapse repetitive detail-page entries (e.g. 20 rows of "View" at /jobs/39/, /jobs/40/, ...)
        deduped = _collapse_detail_duplicates(deduped)

        goals_map = await _generate_goals_for_modules(deduped)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        discovered = [
            DiscoveredModule(
                name=m["name"] or _infer_name_from_url(m["url"]),
                url=m["url"],
                suggested_goal=goals_map.get(
                    m["url"],
                    f"Verify {m['name'] or _infer_name_from_url(m['url'])} loads and key interactions work.",
                ),
                depth=m["depth"],
            )
            for m in deduped
        ]

        return DiscoverModulesResponse(
            modules=discovered,
            base_url=base_url,
            discovery_time_ms=elapsed_ms,
            total_discovered=len(discovered),
        )

    finally:
        for obj, label in [(page, "page"), (context, "context"), (browser, "browser"), (playwright, "playwright")]:
            if obj:
                try:
                    await obj.close() if label != "playwright" else await obj.stop()
                except Exception:
                    pass
