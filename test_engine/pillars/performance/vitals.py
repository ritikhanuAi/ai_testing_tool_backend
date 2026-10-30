from playwright.async_api import BrowserContext, Page
from typing import Dict, Any, Tuple, List
from app.common.constants import FindingSeverity
from test_engine.pillars.base import PillarFinding

class CoreWebVitalsScanner:
    """Collects browser-side lab estimates without treating missing metrics as zero."""

    INIT_SCRIPT = r"""(() => {
        if (window.__aiTestingPerf) return;
        const state = { lcp: null, cls: null, inp: null, tbt: null, longTasks: 0, support: {} };
        let clsSessionValue = 0;
        let clsSessionStart = 0;
        let clsLastTime = 0;
        const interactions = new Map();
        try {
            new PerformanceObserver(list => {
                const entries = list.getEntries();
                if (entries.length) state.lcp = entries[entries.length - 1].startTime;
            }).observe({type: 'largest-contentful-paint', buffered: true});
            state.support.lcp = true;
        } catch (_) {}
        try {
            new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                    if (entry.hadRecentInput) continue;
                    const time = entry.startTime;
                    if (!clsSessionStart || time - clsLastTime > 1000 || time - clsSessionStart > 5000) {
                        clsSessionStart = time;
                        clsSessionValue = 0;
                    }
                    clsSessionValue += entry.value;
                    clsLastTime = time;
                    state.cls = Math.max(state.cls, clsSessionValue);
                }
            }).observe({type: 'layout-shift', buffered: true});
            state.cls = 0;
            state.support.cls = true;
        } catch (_) {}
        try {
            new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                    if (!entry.interactionId) continue;
                    interactions.set(entry.interactionId, Math.max(
                        interactions.get(entry.interactionId) || 0,
                        entry.duration
                    ));
                }
                const values = Array.from(interactions.values()).sort((a, b) => a - b);
                if (values.length) state.inp = values[Math.max(0, Math.ceil(values.length * 0.98) - 1)];
            }).observe({type: 'event', buffered: true, durationThreshold: 40});
            state.support.inp = true;
        } catch (_) {}
        try {
            new PerformanceObserver(list => {
                for (const entry of list.getEntries()) {
                    state.longTasks += 1;
                    state.tbt += Math.max(0, entry.duration - 50);
                }
            }).observe({type: 'longtask', buffered: true});
            state.tbt = 0;
            state.support.long_tasks = true;
        } catch (_) {}
        Object.defineProperty(window, '__aiTestingPerf', {value: state, configurable: false});
    })();"""

    @classmethod
    async def install_observer(cls, context: BrowserContext) -> None:
        """Install metric observers before any page scripts execute."""
        await context.add_init_script(script=cls.INIT_SCRIPT)

    @staticmethod
    async def collect_metrics(page: Page) -> Dict[str, Any]:
        return await page.evaluate("""() => {
            const nav = performance.getEntriesByType('navigation')[0];
            const paints = performance.getEntriesByType('paint');
            const fcpEntry = paints.find(entry => entry.name === 'first-contentful-paint');
            const observer = window.__aiTestingPerf || {};
            const resources = performance.getEntriesByType('resource');
            const byType = {};
            let transferSize = 0;
            let encodedBodySize = 0;
            let unavailableSizeCount = 0;
            const oversizedAssets = [];
            for (const resource of resources) {
                const type = resource.initiatorType || 'other';
                byType[type] = (byType[type] || 0) + 1;
                transferSize += resource.transferSize || 0;
                encodedBodySize += resource.encodedBodySize || 0;
                if (!resource.transferSize && !resource.encodedBodySize) unavailableSizeCount += 1;
                const size = Math.max(resource.transferSize || 0, resource.encodedBodySize || 0);
                if (size > 1024 * 1024) {
                    oversizedAssets.push({url: resource.name, size_bytes: size, initiator_type: type});
                }
            }
            return {
                fcp_ms: fcpEntry ? Math.round(fcpEntry.startTime) : null,
                lcp_ms: Number.isFinite(observer.lcp) ? Math.round(observer.lcp) : null,
                cls: Number.isFinite(observer.cls) ? Number(observer.cls.toFixed(4)) : null,
                inp_ms: Number.isFinite(observer.inp) ? Math.round(observer.inp) : null,
                tbt_ms: Number.isFinite(observer.tbt) ? Math.round(observer.tbt) : null,
                observer_support: observer.support || {},
                long_task_count: observer.longTasks || 0,
                ttfb_ms: nav ? Math.round(nav.responseStart - nav.requestStart) : null,
                dom_content_loaded_ms: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
                load_ms: nav && nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null,
                resource_count: resources.length,
                resource_count_by_type: byType,
                transfer_size_bytes: transferSize,
                encoded_body_size_bytes: encodedBodySize,
                resource_size_unavailable_count: unavailableSizeCount,
                oversized_assets: oversizedAssets.slice(0, 20)
            };
        }""")

    @staticmethod
    async def capture_vitals(page: Page) -> Tuple[Dict[str, Any], List[PillarFinding]]:
        findings: List[PillarFinding] = []
        metrics = await CoreWebVitalsScanner.collect_metrics(page)
        thresholds = (
            ("lcp_ms", 2500, 4000, "LCP", "Improve the largest above-the-fold resource and reduce render-blocking work."),
            ("cls", 0.1, 0.25, "CLS", "Reserve layout space for images, ads, embeds, and dynamically inserted content."),
            ("inp_ms", 200, 500, "INP", "Reduce long main-thread tasks and keep event handlers small."),
        )
        for key, good_limit, poor_limit, label, fix in thresholds:
            value = metrics.get(key)
            if value is None:
                continue
            if value > good_limit:
                severity = FindingSeverity.HIGH if value > poor_limit else FindingSeverity.MEDIUM
                findings.append(PillarFinding(
                    id=f"PERF-CORE-WEB-VITAL-{label}",
                    title=f"{label} is above the recommended threshold ({value}{'ms' if key.endswith('_ms') else ''})",
                    description=f"Measured {label}={value}; recommended good threshold is at most {good_limit}.",
                    severity=severity,
                    suggested_fix=fix,
                ))

        ttfb = metrics.get("ttfb_ms")
        if ttfb is not None and ttfb > 800:
            findings.append(PillarFinding(
                id="PERF-SLOW-TTFB",
                title=f"Slow server response start (TTFB: {ttfb}ms)",
                description="The browser waited over 800ms for the first response byte in this lab run.",
                severity=FindingSeverity.HIGH if ttfb > 1800 else FindingSeverity.MEDIUM,
                suggested_fix="Inspect server processing time, cache behavior, and network latency.",
            ))
        return metrics, findings
