import asyncio
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit, urlunsplit

from app.common.constants import FindingSeverity, PillarType
from test_engine.crawl.site_crawler import CrawlResult, redact_url
from test_engine.pillars.base import BasePillarScanner, PillarFinding, PillarScanResult
from test_engine.pillars.security.cookies import CookieSecurityScanner
from test_engine.pillars.security.exposures import SensitiveExposureScanner
from test_engine.pillars.security.headers import SecurityHeadersScanner
from test_engine.pillars.security.tls import TLSScanner
from test_engine.policy.allowlist import TargetAllowlistValidator
from test_engine.scoring.rubrics import calculate_pillar_deductions


class SecurityScanner(BasePillarScanner):
    """Run bounded passive security checks across every page discovered by the crawler."""

    MAX_REPORTED_FINDINGS = 300
    MAX_ORIGINS_TO_PROBE = 10

    @property
    def pillar_type(self) -> PillarType:
        return PillarType.SECURITY

    @staticmethod
    def _origin(url: str) -> Optional[str]:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))

    @staticmethod
    def _make_finding(
        *,
        finding_id: str,
        title: str,
        description: str,
        severity: FindingSeverity,
        url: str,
        suggested_fix: str,
    ) -> PillarFinding:
        return PillarFinding(
            id=finding_id,
            title=title,
            description=description,
            severity=severity,
            selector_or_url=redact_url(url),
            suggested_fix=suggested_fix,
        )

    async def scan(
        self,
        page,
        target_url: str,
        crawl_result: Optional[CrawlResult] = None,
        request_rate_limit: int = 10,
        **kwargs: Any,
    ) -> PillarScanResult:
        pages = crawl_result.pages if crawl_result else []
        findings: List[PillarFinding] = []
        status_counts: Dict[str, int] = {}
        header_pages = 0
        cookie_count = 0

        for crawled_page in pages:
            url = crawled_page.final_url or crawled_page.url
            if crawled_page.status_code is not None:
                header_findings, _ = SecurityHeadersScanner.audit_headers(crawled_page.response_headers, redact_url(url))
                findings.extend(header_findings)
                header_pages += 1
            cookie_findings, _ = CookieSecurityScanner.audit_cookies(crawled_page.cookies, redact_url(url))
            findings.extend(cookie_findings)
            cookie_count += len(crawled_page.cookies)

            status_key = str(crawled_page.status_code) if crawled_page.status_code is not None else "no_response"
            status_counts[status_key] = status_counts.get(status_key, 0) + 1
            if crawled_page.status_code is not None and crawled_page.status_code >= 500:
                findings.append(self._make_finding(
                    finding_id="SEC-SERVER-ERROR-PAGE",
                    title=f"Server error returned for {urlsplit(url).path or '/'}",
                    description=f"The page returned HTTP {crawled_page.status_code} during the bounded crawl.",
                    severity=FindingSeverity.HIGH,
                    url=url,
                    suggested_fix="Inspect the server-side error and ensure error responses do not expose stack traces or secrets.",
                ))

            observations = crawled_page.page_observations
            for resource_url in observations.get("mixed_content", [])[:10]:
                findings.append(self._make_finding(
                    finding_id="SEC-MIXED-CONTENT",
                    title="HTTPS page loaded an insecure HTTP resource",
                    description=f"The browser observed an HTTP resource on this HTTPS page: {redact_url(resource_url)}",
                    severity=FindingSeverity.HIGH,
                    url=url,
                    suggested_fix="Serve the resource over HTTPS and update the page reference.",
                ))

            for form_action in observations.get("insecure_form_actions", [])[:10]:
                if url.lower().startswith("https://"):
                    findings.append(self._make_finding(
                        finding_id="SEC-INSECURE-FORM-ACTION",
                        title="Secure page submits a form over HTTP",
                        description=f"Form action sends submitted data to an unencrypted URL: {redact_url(form_action)}",
                        severity=FindingSeverity.CRITICAL,
                        url=url,
                        suggested_fix="Use HTTPS for the form action and reject downgrade redirects.",
                    ))

            for password_field in observations.get("password_fields", [])[:10]:
                action = password_field.get("form_action", "")
                if not url.lower().startswith("https://") or action.lower().startswith("http://"):
                    findings.append(self._make_finding(
                        finding_id="SEC-PASSWORD-OVER-HTTP",
                        title="Password field is exposed on an unencrypted page or form",
                        description=f"A password control is present on {redact_url(url)}; form action is {redact_url(action) if action else 'not declared'}.",
                        severity=FindingSeverity.CRITICAL,
                        url=url,
                        suggested_fix="Serve the page and form submission over HTTPS and enable HSTS.",
                    ))

            if crawled_page.navigation_error:
                findings.append(self._make_finding(
                    finding_id="SEC-CRAWL-NAVIGATION-ERROR",
                    title="Page could not be fully inspected",
                    description=f"The scanner could not inspect this in-scope page: {crawled_page.navigation_error}",
                    severity=FindingSeverity.MEDIUM,
                    url=redact_url(url),
                    suggested_fix="Check the route availability and repeat the authorized scan.",
                ))

        origins: List[str] = []
        seen_origins: Set[str] = set()
        for crawled_page in pages:
            origin = self._origin(crawled_page.final_url or crawled_page.url)
            if origin and origin not in seen_origins:
                seen_origins.add(origin)
                origins.append(origin)

        tls_metrics: Dict[str, Dict[str, Any]] = {}
        exposure_checks = 0
        origins_skipped_unsafe = 0
        origins_probed = 0
        tls_probe_failed_origins: List[str] = []
        for origin in origins[:self.MAX_ORIGINS_TO_PROBE]:
            hostname = urlsplit(origin).hostname or ""
            public, _ = await asyncio.to_thread(TargetAllowlistValidator.assert_public_host, hostname)
            if not public:
                origins_skipped_unsafe += 1
                continue
            exposure_findings, _ = await SensitiveExposureScanner.check_exposures(
                origin, request_rate_limit=request_rate_limit
            )
            findings.extend(exposure_findings)
            exposure_checks += len(SensitiveExposureScanner.COMMON_EXPOSURE_PATHS)
            await asyncio.sleep(1.0 / max(request_rate_limit, 1))
            tls_findings, _, origin_tls_metrics = await asyncio.to_thread(TLSScanner.audit_tls, origin)
            findings.extend(tls_findings)
            tls_metrics[origin] = origin_tls_metrics
            if origin_tls_metrics.get("tls_probe_error"):
                tls_probe_failed_origins.append(redact_url(origin))
                findings.append(self._make_finding(
                    finding_id="SEC-TLS-PROBE-FAILED",
                    title="TLS configuration could not be verified",
                    description="The scanner could not complete a TLS handshake, so certificate and protocol settings remain unverified.",
                    severity=FindingSeverity.MEDIUM,
                    url=origin,
                    suggested_fix="Check TLS reachability from the scan worker and verify certificate and protocol settings independently.",
                ))
            origins_probed += 1
            await asyncio.sleep(1.0 / max(request_rate_limit, 1))

        if len(origins) > self.MAX_ORIGINS_TO_PROBE:
            findings.append(self._make_finding(
                finding_id="SEC-ORIGIN-PROBE-LIMIT",
                title="Security probes were limited to the first ten approved origins",
                description=f"The crawl found {len(origins)} distinct origins; TLS and exposure probes ran on {self.MAX_ORIGINS_TO_PROBE}.",
                severity=FindingSeverity.INFO,
                url=redact_url(target_url),
                suggested_fix="Review the allowed origin list and explicitly include only environments intended for scanning.",
            ))

        if not pages:
            findings.append(self._make_finding(
                finding_id="SEC-CRAWL-NO-PAGES",
                title="Security pillar could not inspect any pages",
                description="The crawler produced no page observations, so the security score has no coverage.",
                severity=FindingSeverity.HIGH,
                url=redact_url(target_url),
                suggested_fix="Check target authorization, reachability, and the crawl error details before treating the score as meaningful.",
            ))
        elif not any(page.navigation_error is None for page in pages):
            findings.append(self._make_finding(
                finding_id="SEC-CRAWL-NO-USABLE-PAGES",
                title="Security pillar could not inspect a page successfully",
                description="Every attempted page had a navigation error; security observations and the resulting grade have very limited coverage.",
                severity=FindingSeverity.HIGH,
                url=target_url,
                suggested_fix="Check target reachability and the crawl errors before relying on the security score.",
            ))

        score, deductions = calculate_pillar_deductions(findings)
        finding_count = len(findings)
        finding_severity_counts = {
            member.value: sum(finding.severity == member for finding in findings)
            for member in FindingSeverity
        }
        findings = findings[:self.MAX_REPORTED_FINDINGS]
        usable_pages = sum(page.navigation_error is None for page in pages)
        crawl_limited = bool(crawl_result and (
            crawl_result.truncated_by_page_limit
            or crawl_result.truncated_by_timeout
            or crawl_result.truncated_by_link_limit
            or crawl_result.sitemap_truncated
            or crawl_result.skipped_depth_limit
            or crawl_result.skipped_unsafe
        ))
        if not usable_pages:
            status = "FAILED"
            score = 0.0
        elif score < 60 or finding_severity_counts[FindingSeverity.CRITICAL.value]:
            status = "FAILED"
        elif (
            score < 85
            or finding_severity_counts[FindingSeverity.HIGH.value]
            or crawl_limited
            or usable_pages < len(pages)
            or len(origins) > self.MAX_ORIGINS_TO_PROBE
            or origins_skipped_unsafe
            or tls_probe_failed_origins
        ):
            status = "WARNING"
        else:
            status = "PASSED"
        return PillarScanResult(
            pillar=self.pillar_type,
            score=score,
            status=status,
            findings=findings,
            metrics={
                "pages_scanned": len(pages),
                "pages_with_observations": usable_pages,
                "pages_with_navigation_errors": sum(page.navigation_error is not None for page in pages),
                "response_headers_audited": header_pages,
                "cookies_audited": cookie_count,
                "origins_found": len(origins),
                "origins_probed": origins_probed,
                "origins_skipped_unsafe": origins_skipped_unsafe,
                "tls_probe_failed_origins": tls_probe_failed_origins,
                "exposure_paths_checked": exposure_checks,
                "tls_metrics_by_origin": tls_metrics,
                "page_status_counts": status_counts,
                "mixed_content_pages": sum(bool(item.page_observations.get("mixed_content")) for item in pages),
                "pages_with_password_fields": sum(bool(item.page_observations.get("password_fields")) for item in pages),
                "crawl_coverage": crawl_result.report_summary() if crawl_result else {},
                "finding_count": finding_count,
                "finding_severity_counts": finding_severity_counts,
                "reported_finding_count": len(findings),
                "findings_truncated": finding_count > len(findings),
                "score_deductions": deductions,
                "score_unavailable": usable_pages == 0,
                "score_note": (
                    "No page was inspected successfully; 0 avoids presenting a clean security grade without evidence."
                    if usable_pages == 0 else "Score deductions are based on checks completed for crawled pages and approved origins."
                ),
                "checks": [
                    "security_headers_and_csp",
                    "cookie_flags",
                    "mixed_content",
                    "insecure_form_actions",
                    "password_transport",
                    "server_error_responses",
                    "common_sensitive_file_exposure",
                    "tls_certificate_and_protocol",
                ],
            },
        )
