import asyncio
import re
import time
import xml.etree.ElementTree as ElementTree
from collections import deque
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, Field
from playwright.async_api import Page
from test_engine.pillars.performance.vitals import CoreWebVitalsScanner


_SIDE_EFFECT_PATH = re.compile(
    r"(?:^|/)(?:logout|log-out|signout|sign-out|delete|remove|unsubscribe|checkout|purchase|payment|pay|transfer|publish|send)(?:/|$)",
    re.IGNORECASE,
)
_SIDE_EFFECT_QUERY_KEYS = {
    "action", "confirm", "delete", "logout", "signout", "unsubscribe", "purchase", "checkout",
}
_TRACKING_QUERY_KEYS = {"gclid", "fbclid", "msclkid"}
_SECURITY_RESPONSE_HEADERS = {
    "content-security-policy", "strict-transport-security", "x-frame-options",
    "x-content-type-options", "referrer-policy", "permissions-policy", "server", "x-powered-by",
}
_SENSITIVE_QUERY_KEY = re.compile(r"(?:pass(?:word|wd)?|token|jwt|secret|api[_-]?key|auth|session|sid|cookie|credential|email|phone|code)", re.I)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.I)
_SECRET_ASSIGNMENT = re.compile(r"(?i)(password|passwd|token|jwt|secret|api[_-]?key|auth|session|cookie)=([^\s&;,]+)")


def redact_url(url: str) -> str:
    """Hide likely credentials and personal data in report-visible URLs."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return f"{parsed.scheme or 'url'}:[redacted]"
        hostname = parsed.hostname or ""
        host = f"[{hostname}]" if ":" in hostname else hostname
        safe_netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
        query = [
            (key, "[redacted]" if _SENSITIVE_QUERY_KEY.search(key) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        return urlunsplit((parsed.scheme, safe_netloc, parsed.path, urlencode(query), ""))
    except (TypeError, ValueError):
        return "[invalid URL]"


def redact_text(value: str) -> str:
    def clean_url(match: re.Match) -> str:
        raw = match.group(0)
        suffix = ""
        while raw and raw[-1] in ".,);]":
            suffix = raw[-1] + suffix
            raw = raw[:-1]
        return redact_url(raw) + suffix

    return _SECRET_ASSIGNMENT.sub(r"\1=[redacted]", _URL_IN_TEXT.sub(clean_url, value))


class CrawledPage(BaseModel):
    """Redacted metadata collected from one in-scope page visit."""

    url: str
    final_url: str
    depth: int = Field(ge=0)
    status_code: Optional[int] = None
    response_headers: Dict[str, str] = Field(default_factory=dict)
    cookies: List[Dict[str, Any]] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)
    page_observations: Dict[str, Any] = Field(default_factory=dict)
    navigation_error: Optional[str] = None


class CrawlResult(BaseModel):
    """Crawl coverage and the ordered page inventory for one scan."""

    start_url: str
    pages: List[CrawledPage] = Field(default_factory=list)
    discovered_url_count: int = 0
    skipped_out_of_scope: int = 0
    skipped_unsafe: int = 0
    skipped_link_limit: int = 0
    skipped_depth_limit: int = 0
    truncated_by_page_limit: bool = False
    truncated_by_timeout: bool = False
    truncated_by_link_limit: bool = False
    sitemap_files_checked: int = 0
    sitemap_urls_discovered: int = 0
    sitemap_truncated: bool = False
    max_pages_limit: int = 100
    max_depth_limit: int = 5
    max_links_per_page_limit: int = 500
    request_rate_limit: int = 10
    timeout_seconds_limit: int = 300
    elapsed_seconds: float = 0.0

    def report_summary(self) -> Dict[str, Any]:
        summary = self.model_dump(exclude={"pages"})
        summary["start_url"] = redact_url(self.start_url)
        summary["pages_attempted"] = len(self.pages)
        summary["pages_with_response"] = sum(page.status_code is not None for page in self.pages)
        summary["pages_with_navigation_errors"] = sum(page.navigation_error is not None for page in self.pages)
        summary["max_depth_reached"] = max((page.depth for page in self.pages), default=0)
        summary["coverage_complete"] = not any((
            self.truncated_by_page_limit,
            self.truncated_by_timeout,
            self.truncated_by_link_limit,
            self.sitemap_truncated,
            self.skipped_depth_limit,
            self.skipped_unsafe,
            any(page.navigation_error for page in self.pages),
        ))
        return summary


class SiteCrawler:
    """Breadth-first crawl of safe HTTP(S) links within explicitly allowed hosts."""

    DEFAULT_MAX_PAGES = 100
    DEFAULT_MAX_DEPTH = 5
    MAX_LINKS_PER_PAGE = 500
    MAX_SITEMAP_FILES = 5
    MAX_SITEMAP_URLS = 1000

    @classmethod
    async def _discover_sitemap_urls(
        cls,
        page: Page,
        start_url: str,
        allowed_domains: Set[str],
        root_port: Optional[int],
        deadline: float,
    ) -> Tuple[List[str], int, bool, int, int]:
        start = urlsplit(start_url)
        origin = urlunsplit((start.scheme, start.netloc, "/", "", ""))
        robots_url = urljoin(origin, "/robots.txt")
        sitemap_candidates: List[str] = []
        try:
            response = await page.goto(
                robots_url,
                wait_until="domcontentloaded",
                timeout=max(1, min(5000, int((deadline - time.monotonic()) * 1000))),
            )
            if response and response.status == 200:
                robots_text = await page.evaluate(
                    "() => (document.body?.innerText || document.documentElement?.textContent || '').slice(0, 1000000)"
                )
                sitemap_candidates.extend(
                    match.group(1).strip()
                    for match in re.finditer(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots_text)
                )
        except Exception:
            pass
        explicitly_listed = bool(sitemap_candidates)
        if not sitemap_candidates:
            sitemap_candidates.append(urljoin(origin, "/sitemap.xml"))

        visited_sitemaps: Set[str] = set()
        discovered: List[str] = []
        discovered_set: Set[str] = set()
        truncated = False
        files_checked = 0
        skipped_out_of_scope = 0
        skipped_unsafe = 0
        while sitemap_candidates and files_checked < cls.MAX_SITEMAP_FILES:
            if time.monotonic() >= deadline:
                truncated = True
                break
            candidate = sitemap_candidates.pop(0)
            normalized, classification = cls._normalize_link(candidate, origin, allowed_domains, root_port)
            if not normalized:
                if classification == "out_of_scope":
                    skipped_out_of_scope += 1
                elif classification == "unsafe":
                    skipped_unsafe += 1
                continue
            if normalized in visited_sitemaps:
                continue
            visited_sitemaps.add(normalized)
            try:
                response = await page.goto(
                    normalized,
                    wait_until="domcontentloaded",
                    timeout=max(1, min(5000, int((deadline - time.monotonic()) * 1000))),
                )
                if not response or response.status != 200:
                    if explicitly_listed:
                        truncated = True
                    continue
                xml_source = await page.evaluate(
                    "() => (new XMLSerializer().serializeToString(document).slice(0, 1000000))"
                )
                if "<!doctype" in xml_source.lower() or "<!entity" in xml_source.lower():
                    if explicitly_listed:
                        truncated = True
                    continue
                root = ElementTree.fromstring(xml_source)
                files_checked += 1
                is_index = root.tag.rsplit("}", 1)[-1].lower() == "sitemapindex"
                for element in root.iter():
                    if element.tag.rsplit("}", 1)[-1].lower() != "loc" or not element.text:
                        continue
                    location, classification = cls._normalize_link(element.text.strip(), normalized, allowed_domains, root_port)
                    if not location:
                        if classification == "out_of_scope":
                            skipped_out_of_scope += 1
                        elif classification == "unsafe":
                            skipped_unsafe += 1
                        continue
                    if is_index:
                        if location not in visited_sitemaps and location not in sitemap_candidates:
                            sitemap_candidates.append(location)
                    elif location not in discovered_set:
                        discovered_set.add(location)
                        if len(discovered) < cls.MAX_SITEMAP_URLS:
                            discovered.append(location)
                        else:
                            truncated = True
            except Exception:
                if explicitly_listed:
                    truncated = True
                continue

        if sitemap_candidates:
            truncated = True
        return discovered, files_checked, truncated, skipped_out_of_scope, skipped_unsafe

    @staticmethod
    def _host_is_allowed(hostname: str, allowed_domains: Set[str]) -> bool:
        host = hostname.lower().rstrip(".")
        for raw_pattern in allowed_domains:
            pattern = raw_pattern.lower().strip().rstrip(".")
            if pattern.startswith("*."):
                suffix = pattern[2:]
                if host != suffix and host.endswith("." + suffix):
                    return True
            elif host == pattern:
                return True
        return False

    @classmethod
    def _normalize_link(
        cls,
        href: str,
        base_url: str,
        allowed_domains: Set[str],
        root_port: Optional[int],
    ) -> Tuple[Optional[str], str]:
        try:
            absolute = urljoin(base_url, href.strip())
            parsed = urlsplit(absolute)
            base = urlsplit(base_url)
            if parsed.scheme.lower() not in ("http", "https"):
                return None, "ignored"
            if not parsed.hostname:
                return None, "unsafe"
            if parsed.username or parsed.password:
                return None, "unsafe"
            if not cls._host_is_allowed(parsed.hostname, allowed_domains):
                return None, "out_of_scope"
            port = parsed.port
            standard_port = 443 if parsed.scheme.lower() == "https" else 80
            if port not in (None, standard_port) and not (
                parsed.scheme.lower() == base.scheme.lower() and port == root_port
            ):
                return None, "out_of_scope"

            path = unquote(parsed.path or "/")
            if _SIDE_EFFECT_PATH.search(path):
                return None, "unsafe"
            query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                     if key.lower() not in _TRACKING_QUERY_KEYS]
            if any(key.lower() in _SIDE_EFFECT_QUERY_KEYS for key, _ in query):
                return None, "unsafe"
            normalized_query = urlencode(query)
            return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", normalized_query, "")), "ok"
        except (TypeError, ValueError):
            return None, "unsafe"

    @classmethod
    async def crawl(
        cls,
        page: Page,
        start_url: str,
        allowed_domains: Set[str],
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        timeout_seconds: int = 300,
        max_links_per_page: int = MAX_LINKS_PER_PAGE,
        request_rate_limit: int = 10,
        cancellation_check: Optional[Callable[[], Awaitable[bool]]] = None,
    ) -> CrawlResult:
        """Visit pages breadth-first within explicit page, depth, time, and host limits."""
        if max_pages < 1 or max_depth < 0 or timeout_seconds < 1 or max_links_per_page < 1:
            raise ValueError("Crawl limits must be positive (depth may be zero)")
        try:
            start_port = urlsplit(start_url).port
        except ValueError as error:
            raise ValueError("The starting URL has an invalid port") from error
        start = cls._normalize_link(start_url, start_url, allowed_domains, start_port)[0]
        if not start:
            raise ValueError("The starting URL is not a safe in-scope HTTP(S) URL")

        started = time.monotonic()
        result = CrawlResult(
            start_url=start,
            max_pages_limit=max_pages,
            max_depth_limit=max_depth,
            max_links_per_page_limit=min(max_links_per_page, cls.MAX_LINKS_PER_PAGE),
            request_rate_limit=max(request_rate_limit, 1),
            timeout_seconds_limit=timeout_seconds,
        )
        queue = deque([(start, 0)])
        enqueued: Set[str] = {start}
        visited: Set[str] = set()
        depth_limited: Set[str] = set()
        delay = 1.0 / max(request_rate_limit, 1)
        last_navigation = 0.0
        max_links_per_page = min(max_links_per_page, cls.MAX_LINKS_PER_PAGE)

        # Fresh lab measurements should not reuse resources cached by an earlier crawled route.
        cdp_session = None
        try:
            cdp_session = await page.context.new_cdp_session(page)
            await cdp_session.send("Network.setCacheDisabled", {"cacheDisabled": True})
        except Exception:
            cdp_session = None

        try:
            sitemap_deadline = min(started + timeout_seconds, time.monotonic() + max(2.0, min(15.0, timeout_seconds * 0.1)))
            sitemap_urls, sitemap_files_checked, sitemap_truncated, sitemap_out_of_scope, sitemap_unsafe = await cls._discover_sitemap_urls(
                page,
                start,
                allowed_domains,
                start_port,
                sitemap_deadline,
            )
            result.sitemap_files_checked = sitemap_files_checked
            result.sitemap_truncated = sitemap_truncated
            result.sitemap_urls_discovered = len(sitemap_urls)
            result.skipped_out_of_scope += sitemap_out_of_scope
            result.skipped_unsafe += sitemap_unsafe
            for sitemap_url in sitemap_urls:
                if sitemap_url not in enqueued:
                    enqueued.add(sitemap_url)
                    if max_depth >= 1:
                        queue.append((sitemap_url, 1))
                    else:
                        depth_limited.add(sitemap_url)

            while queue and len(result.pages) < max_pages:
                if cancellation_check and await cancellation_check():
                    raise asyncio.CancelledError("Scan cancelled during site crawl")
                elapsed = time.monotonic() - started
                if elapsed >= timeout_seconds:
                    result.truncated_by_timeout = True
                    break
                current_url, depth = queue.popleft()
                if current_url in visited:
                    continue
                visited.add(current_url)

                remaining_delay = delay - (time.monotonic() - last_navigation)
                if last_navigation and remaining_delay > 0:
                    await asyncio.sleep(remaining_delay)
                last_navigation = time.monotonic()

                status_code: Optional[int] = None
                headers: Dict[str, str] = {}
                navigation_error: Optional[str] = None
                observations: Dict[str, Any] = {}
                js_error_count = [0]
                console_error_count = [0]
                failed_request_count = [0]
                failed_request_urls: List[str] = []
                failed_resource_statuses: List[int] = []

                def on_page_error(error: Exception) -> None:
                    js_error_count[0] += 1

                def on_console(message) -> None:
                    if message.type == "error":
                        console_error_count[0] += 1

                def on_request_failed(request) -> None:
                    hostname = urlsplit(request.url).hostname or ""
                    if cls._host_is_allowed(hostname, allowed_domains):
                        failed_request_count[0] += 1
                        if len(failed_request_urls) < 10:
                            parsed_request_url = urlsplit(request.url)
                            failed_request_urls.append(urlunsplit((
                                parsed_request_url.scheme, parsed_request_url.netloc, parsed_request_url.path, "", ""
                            )))

                def on_response(response) -> None:
                    if response.status < 400 or response.request.is_navigation_request():
                        return
                    hostname = urlsplit(response.url).hostname or ""
                    if cls._host_is_allowed(hostname, allowed_domains) and len(failed_resource_statuses) < 20:
                        failed_resource_statuses.append(response.status)

                page.on("pageerror", on_page_error)
                page.on("console", on_console)
                page.on("requestfailed", on_request_failed)
                page.on("response", on_response)
                try:
                    remaining_ms = max(1, int((timeout_seconds - (time.monotonic() - started)) * 1000))
                    response = await page.goto(
                        current_url,
                        wait_until="domcontentloaded",
                        timeout=min(30000, remaining_ms),
                    )
                    if response:
                        status_code = response.status
                        headers = {
                            str(key).lower(): str(value)
                            for key, value in (await response.all_headers()).items()
                            if str(key).lower() in _SECURITY_RESPONSE_HEADERS
                        }
                    load_wait_ms = min(4000, max(0, int((timeout_seconds - (time.monotonic() - started)) * 1000)))
                    if load_wait_ms:
                        try:
                            await page.wait_for_load_state("load", timeout=load_wait_ms)
                        except Exception:
                            pass
                    links, observations = await page.evaluate(
                        """() => ({
                            links: Array.from(document.querySelectorAll('a[href]'))
                                .map(anchor => anchor.href).filter(Boolean).slice(0, 500),
                        link_count_total: document.querySelectorAll('a[href]').length,
                            page: {
                                title: document.title || '',
                                mixed_content: performance.getEntriesByType('resource')
                                    .map(entry => entry.name).filter(url => location.protocol === 'https:' && url.startsWith('http:')).slice(0, 100),
                                insecure_form_actions: Array.from(document.forms)
                                .map(form => form.action).filter(url => url.startsWith('http:')).slice(0, 30),
                                password_fields: Array.from(document.querySelectorAll('input[type="password"]')).map(input => ({
                                    autocomplete: input.autocomplete || '',
                                    form_action: input.form ? input.form.action : '',
                                    form_method: input.form ? input.form.method.toUpperCase() : ''
                                })).slice(0, 30),
                                forms: document.forms.length,
                                inputs: document.querySelectorAll('input, select, textarea').length,
                                broken_images: Array.from(document.images)
                                    .filter(image => image.complete && image.naturalWidth === 0)
                                    .map(image => {
                                        const url = new URL(image.currentSrc || image.src);
                                        return url.origin + url.pathname;
                                    }).slice(0, 30)
                            }
                        })"""
                    )
                    observations["performance"] = await CoreWebVitalsScanner.collect_metrics(page)
                    observations["page"]["js_error_count"] = js_error_count[0]
                    observations["page"]["console_error_count"] = console_error_count[0]
                    observations["page"]["failed_request_count"] = failed_request_count[0]
                    observations["page"]["failed_request_urls"] = failed_request_urls[:10]
                    observations["page"]["subresource_http_errors"] = failed_resource_statuses[:20]
                    raw_cookies = await page.context.cookies([page.url]) if page.url else []
                    cookies = [{
                        "name": item.get("name", ""),
                        "secure": bool(item.get("secure")),
                        "httpOnly": bool(item.get("httpOnly")),
                        "sameSite": item.get("sameSite", ""),
                        "domain": item.get("domain", ""),
                        "path": item.get("path", "/"),
                    } for item in raw_cookies]
                    final_url = page.url
                except Exception as error:
                    links, cookies = [], []
                    final_url = current_url
                    error_code = re.search(r"net::ERR_[A-Z_]+", str(error))
                    navigation_error = f"{type(error).__name__}: {error_code.group(0) if error_code else 'navigation failed'}"
                finally:
                    page.remove_listener("pageerror", on_page_error)
                    page.remove_listener("console", on_console)
                    page.remove_listener("requestfailed", on_request_failed)
                    page.remove_listener("response", on_response)

                normalized_links: List[str] = []
                if isinstance(observations, dict):
                    result.skipped_link_limit += max(0, observations.get("link_count_total", 0) - max_links_per_page)
                for href in links[:max_links_per_page]:
                    link, classification = cls._normalize_link(
                        href, final_url, allowed_domains, start_port
                    )
                    if classification == "unsafe":
                        result.skipped_unsafe += 1
                    elif classification == "out_of_scope":
                        result.skipped_out_of_scope += 1
                    elif link:
                        normalized_links.append(link)
                        if link not in enqueued:
                            enqueued.add(link)
                            if depth < max_depth:
                                queue.append((link, depth + 1))
                            else:
                                depth_limited.add(link)

                result.pages.append(CrawledPage(
                    url=current_url,
                    final_url=final_url,
                    depth=depth,
                    status_code=status_code,
                    response_headers=headers,
                    cookies=cookies,
                    links=normalized_links,
                    page_observations={
                        **(observations.get("page", {}) if isinstance(observations, dict) else {}),
                        "performance": observations.get("performance", {}) if isinstance(observations, dict) else {},
                    },
                    navigation_error=navigation_error,
                ))
                result.discovered_url_count = len(enqueued)

            result.skipped_depth_limit = len(depth_limited)
            result.truncated_by_page_limit = bool(queue and len(result.pages) >= max_pages)
            result.truncated_by_link_limit = result.skipped_link_limit > 0
            result.truncated_by_timeout = result.truncated_by_timeout or bool(queue and time.monotonic() - started >= timeout_seconds)
        finally:
            if cdp_session:
                try:
                    await cdp_session.send("Network.setCacheDisabled", {"cacheDisabled": False})
                    await cdp_session.detach()
                except Exception:
                    pass
        result.elapsed_seconds = round(time.monotonic() - started, 2)
        return result
