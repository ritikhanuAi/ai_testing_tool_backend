# AI Assistant & Programmer Guidelines (AGENTS.md)

Welcome to the **AI Website Testing Platform** repository.
All developers, automation scripts, and AI coding agents operating in this repository must strictly adhere to the rules and boundaries outlined below.

---

## 1. Prime Directive: 4-Layer Separation

| Layer | Directory | Permitted Responsibilities | FORBIDDEN / NEVER DO |
|---|---|---|---|
| **Web & API** | `app/` | FastAPI Headless REST API, CORS, SQLAlchemy ORM models, auth. | **NEVER** import Playwright or execute browsers directly inside HTTP requests. |
| **Worker Queue** | `workers/` | Celery asynchronous tasks, Redis broker, background orchestration. | **NEVER** run long browser scans synchronously in FastAPI. |
| **Test Engine** | `test_engine/` | Playwright browser runner, 4 quality pillars, policy guardrails, YAML schemas. | **NEVER** import SQLAlchemy, FastAPI, or database models. Must remain pure Python & Pydantic. |
| **Assistive AI** | `ai_engine/` | Ollama offline LLM client, prompt templates, structured output parser. | **NEVER** allow AI to execute arbitrary code or bypass schema enforcement. |

---

## 2. Inviolable Engineering Rules

1. **Browser Execution:** All Playwright browser sessions take minutes and **must** be executed in `workers/tasks/run_scan.py` via Celery, never in `app/`.
2. **Contracts Across Boundaries:** Communication between `app/`, `workers/`, and `test_engine/` must use Pydantic schemas (`TestCaseModel`, `PillarScanResult`, `UnifiedReportData`), never raw SQLAlchemy ORM entities.
3. **Four Quality Pillars:** All pillar scanners must inherit from `BasePillarScanner` and return `PillarScanResult` with scores from `0.0` through `100.0` and status `PASSED`, `WARNING`, `FAILED`, or `SKIPPED`.
4. **Credential Security:** Passwords and tokens must be encrypted with `app.core.security.encrypt_credential()`. Plaintext secrets must never appear in YAML packs, database dumps, logs, or error responses.
5. **Policy Guardrails & SSRF:** 
   - Every scan target must pass `TargetAllowlistValidator.validate_target()` before a run is queued. This checks approved-domain scope and calls `assert_public_host(hostname)` with live DNS resolution.
   - Private IPs (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.0.0/16`, AWS metadata) are blocked unconditionally.
   - Every browser context must attach `ScopeGuard.intercept_route`. It blocks out-of-scope/private hosts and unsupported ports, throttles browser requests, and fails closed (`await route.abort()`). Approved site hosts and the platform's small common-CDN list are allowed.
   - `state_changing` test cases are blocked if the scan profile is read-only.
6. **AI Safety & Enforced Output:**
   - Third-party page contexts passed to LLMs must be enclosed in `<untrusted_page_context>` delimiters.
   - Raw LLM outputs must pass through `SchemaEnforcer.generate_and_validate_with_retry()` with Pydantic validation error feedback loops.
7. **Selector / Service Pattern:** In `app/apps/<domain>/`:
   - `selectors.py` = Read queries only.
   - `services.py` = Writes, business logic, transactions.
8. **Storage Sandboxing:** Screenshots, HAR logs, and exports must reside in `storage/` and be referenced using relative paths (e.g. `screenshots/filename.png`).
9. **Emergency Stop / Scan Cancellation:** Celery scans must check Redis (`cancel:{run_id}`) during crawl visits and between pillars, and stop when a cancellation is requested.
10. **Deterministic Scoring & Fingerprinting:** Deductions are capped at 3 occurrences per unique rule ID, and every finding must generate a deterministic SHA-1 fingerprint for run-to-run tracking.

---

## 3. Current Scan Implementation

Keep code changes, reports, and documentation consistent with this scan flow:

1. `app/apps/test_runs/services.py` validates that the website is active, authorized, verified, publicly resolvable, and in the approved domain scope. It validates the selected test pack/profile before enqueueing the Celery job.
2. `workers/tasks/run_scan.py` creates an isolated Playwright context, installs the performance observers, and calls `test_engine/crawl/site_crawler.py` before the pillars.
3. The crawler follows in-scope anchors and `robots.txt` sitemap entries. Its current limits are 100 page visits, depth 5, 500 links per page, 5 sitemap documents, 1,000 sitemap URLs, and 60% of the scan timeout. It skips likely state-changing routes and never submits forms. Reports must expose partial coverage; do not describe this as exhaustive discovery of every application route.
4. All four pillars consume the crawl inventory. Functional route health, security, performance, and deterministic accessibility checks cover visited pages. Authored test packs start from the starting page and may explicitly navigate to approved URLs; the bounded AI interaction and AI visual review start on the starting page.
5. Security checks response headers/CSP, cookie flags, mixed content, insecure form/password transport, server errors, common exposed endpoints, and TLS certificate/protocol settings. Direct TLS/exposure probes are limited to 10 approved origins. A failed TLS probe is reported as unverified, not clean.
6. Performance uses browser lab APIs (PerformanceObserver, Navigation Timing, and Resource Timing) for paint/vital estimates, observed long-task blocking, and resource sizes. It summarizes page-sample p75 values. These are not real-user field metrics; INP remains unavailable when no interaction was observed.
7. Screenshot-based visual review uses `RemoteLLMClient.generate_with_vision()` and the configured `REMOTE_LLM_BASE_URL`, `REMOTE_LLM_API_KEY`, and `REMOTE_LLM_MODEL`. It sends the starting page's responsive captures and optional before/after interaction captures to the server's `/chat/completions` endpoint as base64 PNG image URLs. The configured server model must support vision input. If the server/model review fails, report it as unavailable instead of a clean result.
8. Reports include crawl coverage, finding counts/severities, deductions, and effective pillar weights. Default weights are functional 35%, security 25%, performance 20%, accessibility 20%, renormalized over enabled pillars. `SKIPPED` pillars are excluded. A pillar with no usable observations receives score 0 and `FAILED`; incomplete coverage produces a warning.

When changing any bound, scanner, measured metric, scoring rule, or coverage scope, update the matching README description and keep the report's status/metrics honest about what was and was not measured.

---

## 4. Verification Commands

Before completing any task, run:

```bash
.venv/bin/pytest -v
```

All 25+ tests in `tests/unit/` and `tests/integration/` must pass with zero errors.

---

For complete guidelines and architecture, see [PROJECT_GUIDELINES.md](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/PROJECT_GUIDELINES.md) and [ARCHITECTURE.md](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/ARCHITECTURE.md).
