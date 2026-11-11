"""Wrapper around Google Lighthouse CLI for performance auditing.

Requires the ``lighthouse`` CLI on PATH (``npm i -g lighthouse``) or Node.js +
``npx`` so ``npx lighthouse`` resolves. When neither is available ``audit()``
returns ``None`` and the performance pillar silently falls back to its
in-browser Core Web Vitals observer.
"""

import asyncio
import json
import shutil
from typing import Any, Dict, List, Optional, Tuple

from app.common.constants import FindingSeverity
from test_engine.crawl.site_crawler import redact_url
from test_engine.pillars.base import PillarFinding


class LighthouseScanner:
    """Runs a performance-only Lighthouse audit against a single URL."""

    TIMEOUT_SECONDS = 180
    CHROME_FLAGS = "--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage"

    @classmethod
    def _resolve_executable(cls) -> Optional[Tuple[str, List[str]]]:
        """Return ``(path, extra_prefix_args)`` for invoking Lighthouse, or ``None``."""
        lighthouse_bin = shutil.which("lighthouse")
        if lighthouse_bin:
            return lighthouse_bin, []
        npx_bin = shutil.which("npx")
        if npx_bin:
            return npx_bin, ["--yes", "lighthouse"]
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls._resolve_executable() is not None

    @classmethod
    async def audit(
        cls,
        url: str,
        *,
        form_factor: str = "mobile",
        timeout_seconds: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run Lighthouse and return the parsed JSON report, or ``None`` on failure."""
        resolved = cls._resolve_executable()
        if not resolved:
            return None
        executable, prefix_args = resolved

        cmd = [
            executable,
            *prefix_args,
            url,
            "--quiet",
            "--output=json",
            "--only-categories=performance",
            f"--form-factor={form_factor}",
            "--chrome-flags=" + cls.CHROME_FLAGS,
            "--output-path=stdout",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError):
            return None

        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds or cls.TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except Exception:
                pass
            return None

        if proc.returncode not in (0, None) or not stdout:
            return None
        try:
            return json.loads(stdout.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _audit_numeric(audits: Dict[str, Any], key: str) -> Optional[float]:
        audit = audits.get(key) or {}
        value = audit.get("numericValue")
        return float(value) if isinstance(value, (int, float)) else None

    @classmethod
    def extract_metrics(cls, report: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the Core Web Vitals + perf score out of a Lighthouse JSON report."""
        audits = report.get("audits", {}) or {}
        categories = report.get("categories", {}) or {}
        category_score = categories.get("performance", {}).get("score")
        performance_score = (
            int(round(category_score * 100)) if isinstance(category_score, (int, float)) else None
        )

        def ms(key: str) -> Optional[int]:
            value = cls._audit_numeric(audits, key)
            return int(round(value)) if value is not None else None

        cls_value = cls._audit_numeric(audits, "cumulative-layout-shift")
        return {
            "performance_score": performance_score,
            "fcp_ms": ms("first-contentful-paint"),
            "lcp_ms": ms("largest-contentful-paint"),
            "tbt_ms": ms("total-blocking-time"),
            "cls": round(cls_value, 4) if cls_value is not None else None,
            "si_ms": ms("speed-index"),
            "tti_ms": ms("interactive"),
            "ttfb_ms": ms("server-response-time"),
            "lighthouse_version": report.get("lighthouseVersion"),
            "form_factor": (report.get("configSettings") or {}).get("formFactor"),
            "fetch_time": report.get("fetchTime"),
            "final_url": report.get("finalDisplayedUrl") or report.get("finalUrl"),
        }

    @classmethod
    def extract_findings(cls, report: Dict[str, Any], page_url: str) -> List[PillarFinding]:
        """Convert Lighthouse audits that failed in the performance category into findings."""
        findings: List[PillarFinding] = []
        audits = report.get("audits", {}) or {}
        performance_category = (report.get("categories") or {}).get("performance") or {}
        category_audit_refs = performance_category.get("auditRefs") or []
        weight_by_id = {ref.get("id"): ref.get("weight", 0) for ref in category_audit_refs}

        for audit_id, audit in audits.items():
            if not isinstance(audit, dict):
                continue
            score = audit.get("score")
            if not isinstance(score, (int, float)) or score >= 0.9:
                continue
            if weight_by_id.get(audit_id, 0) <= 0:
                continue
            title = audit.get("title") or audit_id
            description = (audit.get("description") or "").strip()
            display_value = audit.get("displayValue") or ""
            if score < 0.5:
                severity = FindingSeverity.HIGH
            elif score < 0.75:
                severity = FindingSeverity.MEDIUM
            else:
                severity = FindingSeverity.LOW
            findings.append(PillarFinding(
                id=f"PERF-LH-{audit_id.upper().replace('_', '-')}",
                title=f"Lighthouse: {title}" + (f" ({display_value})" if display_value else ""),
                description=description or f"Lighthouse scored this audit at {round(score, 2)}.",
                severity=severity,
                selector_or_url=redact_url(page_url),
                suggested_fix="Review the Lighthouse recommendation for this audit and apply the specific optimization it suggests.",
            ))

        category_score = performance_category.get("score")
        if isinstance(category_score, (int, float)) and category_score < 0.5:
            findings.append(PillarFinding(
                id="PERF-LH-CATEGORY-SCORE",
                title=f"Lighthouse performance category score is low ({int(round(category_score * 100))}/100)",
                description="Lighthouse rates the overall page performance as poor; multiple audits contribute to this score.",
                severity=FindingSeverity.HIGH,
                selector_or_url=redact_url(page_url),
                suggested_fix="Address the highest-weighted failing Lighthouse audits (LCP, TBT, CLS, FCP, Speed Index) first.",
            ))
        return findings
