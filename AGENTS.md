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
3. **Four Quality Pillars:** All pillar scanners must inherit from `BasePillarScanner` and return `PillarScanResult` with scores strictly between `0.0` and `100.0`.
4. **Credential Security:** Passwords and tokens must be encrypted with `app.core.security.encrypt_credential()`. Plaintext secrets must never appear in YAML packs, database dumps, logs, or error responses.
5. **Policy Guardrails & SSRF:** 
   - Every scan target must pass `TargetAllowlistValidator.assert_public_host(hostname)` with live DNS resolution.
   - Private IPs (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.0.0/16`, AWS metadata) are blocked unconditionally.
   - Every browser context must attach `ScopeGuard.intercept_route` to block external domain egress with fail-closed behavior (`await route.abort()`).
   - `state_changing` test cases are blocked if the scan profile is read-only.
6. **AI Safety & Enforced Output:**
   - Third-party page contexts passed to LLMs must be enclosed in `<untrusted_page_context>` delimiters.
   - Raw LLM outputs must pass through `SchemaEnforcer.generate_and_validate_with_retry()` with Pydantic validation error feedback loops.
7. **Selector / Service Pattern:** In `app/apps/<domain>/`:
   - `selectors.py` = Read queries only.
   - `services.py` = Writes, business logic, transactions.
8. **Storage Sandboxing:** Screenshots, HAR logs, and exports must reside in `storage/` and be referenced using relative paths (e.g. `screenshots/filename.png`).
9. **Emergency Stop / Scan Cancellation:** Celery scans must check Redis (`cancel:{run_id}`) between every pillar and cancel immediately upon user request.
10. **Deterministic Scoring & Fingerprinting:** Deductions are capped at 3 occurrences per unique rule ID, and every finding must generate a deterministic SHA-1 fingerprint for run-to-run tracking.

---

## 3. Verification Commands

Before completing any task, run:

```bash
.venv/bin/pytest -v
```

All 25+ tests in `tests/unit/` and `tests/integration/` must pass with zero errors.

---

For complete guidelines and architecture, see [PROJECT_GUIDELINES.md](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/PROJECT_GUIDELINES.md) and [ARCHITECTURE.md](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/ARCHITECTURE.md).
