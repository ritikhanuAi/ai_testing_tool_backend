from playwright.async_api import Browser, BrowserContext, Page
from typing import Optional, List, Dict, Any, Set
from test_engine.config import default_engine_config
from test_engine.policy.scope_guard import ScopeGuard
import os

class ContextFactory:
    """Creates isolated browser contexts configured with credentials, viewports, and safety guards."""

    @staticmethod
    async def create_isolated_context(
        browser: Browser,
        allowed_domains: Set[str],
        cookies: Optional[List[Dict[str, Any]]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
        viewport_width: Optional[int] = None,
        viewport_height: Optional[int] = None,
        record_har_path: Optional[str] = None
    ) -> BrowserContext:
        """Create a sandboxed browser context isolated from other scans."""
        context_kwargs: Dict[str, Any] = {
            "viewport": {
                "width": viewport_width or default_engine_config.viewport_width,
                "height": viewport_height or default_engine_config.viewport_height,
            },
            "user_agent": default_engine_config.user_agent,
            "ignore_https_errors": False,
        }

        if record_har_path:
            context_kwargs["record_har_path"] = record_har_path

        context = await browser.new_context(**context_kwargs)

        if cookies:
            await context.add_cookies(cookies)

        if custom_headers:
            await context.set_extra_http_headers(custom_headers)

        # Attach Scope Guard to intercept and block out-of-scope egress
        if allowed_domains:
            guard = ScopeGuard(allowed_hostnames=allowed_domains)
            await context.route("**/*", guard.intercept_route)

        return context
