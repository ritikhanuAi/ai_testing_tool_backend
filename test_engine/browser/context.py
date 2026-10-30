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
        record_har_path: Optional[str] = None,
        request_rate_limit: int = 10,
        target_url: str = "",
        record_video_dir: Optional[str] = None,
        is_mobile: bool = False,
    ) -> BrowserContext:
        """Create a sandboxed browser context isolated from other scans."""
        vp_width = viewport_width or default_engine_config.viewport_width
        vp_height = viewport_height or default_engine_config.viewport_height
        context_kwargs: Dict[str, Any] = {
            "viewport": {
                "width": vp_width,
                "height": vp_height,
            },
            "user_agent": default_engine_config.user_agent,
            "ignore_https_errors": False,
        }

        if is_mobile:
            context_kwargs["is_mobile"] = True
            context_kwargs["has_touch"] = True

        if record_video_dir:
            os.makedirs(record_video_dir, exist_ok=True)
            context_kwargs["record_video_dir"] = record_video_dir
            context_kwargs["record_video_size"] = {
                "width": vp_width,
                "height": vp_height,
            }

        if record_har_path:
            context_kwargs["record_har_path"] = record_har_path

        context = await browser.new_context(**context_kwargs)

        if cookies:
            await context.add_cookies(cookies)

        if custom_headers:
            await context.set_extra_http_headers(custom_headers)

        # Attach Scope Guard to intercept and block out-of-scope egress
        guard = ScopeGuard(
            allowed_hostnames=allowed_domains,
            max_requests_per_second=request_rate_limit,
            target_url=target_url,
        )
        await context.route("**/*", guard.intercept_route)

        return context
