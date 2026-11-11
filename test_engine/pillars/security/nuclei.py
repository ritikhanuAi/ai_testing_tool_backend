"""Wrapper around ProjectDiscovery Nuclei for template-driven vulnerability scanning.

Prefers a locally installed ``nuclei`` binary on PATH. Falls back to the
official ``projectdiscovery/nuclei:latest`` Docker image when Docker is
available. Returns ``None`` when neither is present and the security pillar
silently proceeds with its existing passive checks.

Scope: bounded, non-intrusive baseline. The ``intrusive,dos,fuzz`` template
tags are excluded by default. Only approved public origins reach this scanner.
"""

import asyncio
import json
import shutil
from typing import Any, Dict, List, Optional, Tuple

from app.common.constants import FindingSeverity
from test_engine.crawl.site_crawler import redact_url
from test_engine.pillars.base import PillarFinding


_NUCLEI_SEVERITY_MAP = {
    "critical": FindingSeverity.CRITICAL,
    "high": FindingSeverity.HIGH,
    "medium": FindingSeverity.MEDIUM,
    "low": FindingSeverity.LOW,
    "info": FindingSeverity.INFO,
    "unknown": FindingSeverity.LOW,
}


class NucleiScanner:
    """Runs a bounded Nuclei baseline against a list of approved origins."""

    TIMEOUT_SECONDS = 300
    RATE_LIMIT_RPS = 50
    EXCLUDE_TAGS = "intrusive,dos,fuzz"
    DOCKER_IMAGE = "projectdiscovery/nuclei:latest"

    @classmethod
    def _resolve_executable(cls) -> Optional[Tuple[str, List[str], bool]]:
        """Return ``(executable, prefix_args, is_docker)`` or ``None``."""
        nuclei_bin = shutil.which("nuclei")
        if nuclei_bin:
            return nuclei_bin, [], False
        docker_bin = shutil.which("docker")
        if docker_bin:
            return docker_bin, ["run", "--rm", "-i", cls.DOCKER_IMAGE], True
        return None

    @classmethod
    def is_available(cls) -> bool:
        return cls._resolve_executable() is not None

    @classmethod
    async def scan_targets(
        cls,
        targets: List[str],
        *,
        timeout_seconds: Optional[int] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Run Nuclei against ``targets`` and return a list of parsed JSONL findings."""
        if not targets:
            return []
        resolved = cls._resolve_executable()
        if not resolved:
            return None
        executable, prefix_args, _is_docker = resolved

        cmd = [
            executable,
            *prefix_args,
            "-silent",
            "-jsonl",
            "-no-interactsh",
            "-exclude-tags", cls.EXCLUDE_TAGS,
            "-rate-limit", str(cls.RATE_LIMIT_RPS),
            "-timeout", "15",
            "-retries", "1",
            "-disable-update-check",
        ]
        for target in targets:
            cmd.extend(["-u", target])

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

        if proc.returncode not in (0, None) and not stdout:
            return None

        results: List[Dict[str, Any]] = []
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results

    @staticmethod
    def extract_findings(results: List[Dict[str, Any]]) -> List[PillarFinding]:
        """Convert Nuclei JSONL records into PillarFinding objects."""
        findings: List[PillarFinding] = []
        for record in results:
            info = record.get("info") or {}
            template_id = record.get("template-id") or record.get("templateID") or "unknown"
            severity_label = (info.get("severity") or "unknown").lower()
            severity = _NUCLEI_SEVERITY_MAP.get(severity_label, FindingSeverity.LOW)
            matched_url = (
                record.get("matched-at")
                or record.get("matched")
                or record.get("host")
                or record.get("url")
                or ""
            )
            name = info.get("name") or template_id
            description = (info.get("description") or "").strip() or f"Nuclei template '{template_id}' matched."
            tags = info.get("tags") or []
            tag_label = ", ".join(tags) if isinstance(tags, list) else str(tags)
            reference = info.get("reference") or []
            reference_text = (
                reference[0] if isinstance(reference, list) and reference else (reference if isinstance(reference, str) else "")
            )
            classification = info.get("classification") or {}
            cve_ids = classification.get("cve-id") or []
            cve_label = (
                cve_ids[0] if isinstance(cve_ids, list) and cve_ids else (cve_ids if isinstance(cve_ids, str) else "")
            )

            description_parts = [description]
            if tag_label:
                description_parts.append(f"Tags: {tag_label}.")
            if cve_label:
                description_parts.append(f"CVE: {cve_label}.")
            if reference_text:
                description_parts.append(f"See: {reference_text}.")

            findings.append(PillarFinding(
                id=f"SEC-NUCLEI-{template_id.upper().replace('_', '-').replace('/', '-')}",
                title=f"Nuclei: {name}",
                description=" ".join(description_parts),
                severity=severity,
                selector_or_url=redact_url(matched_url) if matched_url else None,
                suggested_fix=(
                    "Review the Nuclei template output and remediate the matched condition. "
                    "Patch affected software, remove exposed artifacts, or tighten configuration as the template describes."
                ),
            ))
        return findings
