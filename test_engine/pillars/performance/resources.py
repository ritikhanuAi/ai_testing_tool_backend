from playwright.async_api import Page
from typing import Dict, Any, Tuple, List
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class ResourceAuditScanner:
    """Audits page asset weights and network transfer size."""

    @staticmethod
    async def audit_resources(page: Page) -> Tuple[Dict[str, Any], List[PillarFinding]]:
        findings: List[PillarFinding] = []

        resource_data = await page.evaluate("""
            () => {
                const resources = performance.getEntriesByType('resource');
                let totalBytes = 0;
                let oversized = [];

                for (const r of resources) {
                    const size = r.transferSize || 0;
                    totalBytes += size;
                    if (size > 1024 * 1024) { // > 1MB
                        oversized.push({ name: r.name, sizeKB: Math.round(size / 1024) });
                    }
                }

                return {
                    total_requests: resources.length,
                    total_transfer_kb: Math.round(totalBytes / 1024),
                    oversized_assets: oversized.slice(0, 5)
                };
            }
        """)

        metrics = resource_data or {"total_requests": 0, "total_transfer_kb": 0, "oversized_assets": []}
        total_kb = metrics.get("total_transfer_kb", 0)

        if total_kb > 3000:
            findings.append(PillarFinding(
                id="PERF-HEAVY-PAGE",
                title=f"Excessive page payload ({round(total_kb / 1024, 2)}MB)",
                description="Total page transfer size exceeds 3.0 MB, degrading mobile experience.",
                severity=FindingSeverity.MEDIUM,
                suggested_fix="Compress images to WebP/AVIF and enable Brotli/Gzip compression on static assets."
            ))

        for asset in metrics.get("oversized_assets", []):
            findings.append(PillarFinding(
                id=f"PERF-OVERSIZED-{asset['name'][:30]}",
                title=f"Oversized asset ({asset['sizeKB']}KB)",
                description=f"Resource {asset['name']} is larger than 1MB.",
                severity=FindingSeverity.LOW,
                selector_or_url=asset["name"],
                suggested_fix="Resize and compress large media assets."
            ))

        return metrics, findings
