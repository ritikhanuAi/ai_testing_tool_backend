# Architecture & Code Standards Guide

> **CRITICAL ARCHITECTURAL CONTRACT**
> This document defines the engineering standards, layer boundaries, and coding rules for the **AI Website Testing Platform**. Every developer and automated assistant working on this codebase must strictly adhere to these rules. **No pull request violating these boundaries should be merged.**

---

## 1. High-Level Layer Boundaries & Separation of Concerns

The project is strictly partitioned into **four independent architectural layers**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Web & API Layer: app/                                                    │
│    • FastAPI Headless REST API, database models (SQLAlchemy), auth, CORS     │
│    • Manages users, target sites, test packs, scan profiles, and reports    │
│    ⚠️ RULE: NEVER import Playwright or execute browsers directly here!       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Enqueues Celery Tasks
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. Background Worker Layer: workers/                                        │
│    • Celery tasks running out-of-process via Redis broker                   │
│    • Bridges app/ database records with test_engine/ execution              │
│    • Handles timeouts (300s), concurrency, and emergency stops              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Invokes pure Python scanners
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Execution Engine: test_engine/                                           │
│    • Playwright browser pool, isolated contexts, action/assertion runners   │
│    • Four Quality Pillars (Functional, Security, Performance, Accessibility)│
│    • Policy Guardrails (Domain allowlist, scope guard, risk check)          │
│    ⚠️ RULE: ZERO database or FastAPI dependencies! Pure Python & Pydantic.   │
└─────────────────────────────────────────────────────────────────────────────┘
                                       ▲
                                       │ Calls for test ideation/diagnostics
┌──────────────────────────────────────┴──────────────────────────────────────┐
│ 4. Assistive AI Layer: ai_engine/                                           │
│    • Local offline Ollama client, prompt templates, structured output parser│
│    • Suggests YAML test cases & explains test failures in plain English     │
│    ⚠️ RULE: AI CANNOT execute browser actions! AI output must pass schema.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. The 10 "Never-Violate" Rules (Code Architecture Commandments)

### Rule 1: No Direct Browser Automation Inside FastAPI (`app/`)
* **Prohibited:** Never instantiate Playwright, launch Chromium, or call `page.goto()` inside any FastAPI route, selector, or service under `app/`.
* **Reason:** Browser scans take seconds to minutes. Running them inside a web request worker blocks uvicorn worker threads and causes HTTP timeouts.
* **Requirement:** All browser runs **must** be queued via Celery tasks in `workers/tasks/run_scan.py`. FastAPI endpoints must return an immediate HTTP `202 Accepted` with a `run_id`.

### Rule 2: `test_engine/` Must Have Zero Database or Web Framework Dependencies
* **Prohibited:** Do not import `SQLAlchemy`, `FastAPI`, `Request`, `Depends`, or any model from `app/` inside `test_engine/`.
* **Reason:** `test_engine/` must remain an isolated, highly testable engine that can be run from the CLI, within Docker workers, or in unit tests without a live database.
* **Requirement:** All data entering or leaving `test_engine/` must use standard **Pydantic contracts** (`TestCaseModel`, `TestPackModel`, `PillarScanResult`, `UnifiedReportData`).

### Rule 3: Four Pillars Must Follow `BasePillarScanner`
* **Requirement:** Every pillar scanner must inherit from `test_engine.pillars.base.BasePillarScanner` and return a `PillarScanResult` containing:
  - `pillar`: One of `PillarType` (`functional`, `security`, `performance`, `accessibility`)
  - `score`: `float` strictly bounded between `0.0` and `100.0`
  - `status`: `"PASSED" | "WARNING" | "FAILED"`
  - `findings`: `List[PillarFinding]` with explicit severity levels
  - `metrics`: `Dict[str, Any]` telemetry data

### Rule 4: Absolute Security for Target Credentials
* **Prohibited:** Never store plaintext passwords or authentication tokens in database tables, log messages, test packs, or error traces.
* **Requirement:** 
  - Passwords and tokens must be encrypted upon ingestion using `app.core.security.encrypt_credential()`.
  - In YAML test cases, credentials are only referenced by their key name (e.g., `credentials_ref: "admin_login"`).
  - Credentials must only be decrypted inside memory within the isolated worker process just prior to the browser login action.

### Rule 5: Scope & Safety Policy Guardrails Cannot Be Bypassed
* **Prohibited:** Never disable target allowlist checks or browser route interception to "make a test work".
* **Requirement:**
  - Before a scan is queued, `TargetAllowlistValidator.validate_target()` must verify that the target URL matches the pre-approved domains.
  - In production, private IP addresses (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.0.0/16`, etc.) are strictly forbidden (SSRF protection).
  - Every Playwright context must attach `ScopeGuard.intercept_route` to ensure the browser cannot wander to unauthorized third-party domains.
  - Test cases flagged with `risk: state_changing` cannot be executed if the scan profile is `allow_state_changing: false` (read-only default).

### Rule 6: AI Output Must Pass Strict Pydantic Schema Validation
* **Prohibited:** Never trust raw LLM output or inject it directly into the database or browser runner.
* **Requirement:**
  - All responses generated by `ai_engine/` must pass through `SchemaEnforcer.parse_test_case()`.
  - If the LLM generates unsupported actions or invalid fields, the parser must reject it before any human review or execution.

### Rule 7: Selector vs. Service Pattern in `app/apps/`
To maintain clean, readable domain modules, each app in `app/apps/<domain>/` must adhere to:
* **`models.py`:** SQLAlchemy ORM entities only. Must inherit from `app.common.models.BaseModel`.
* **`serializers.py`:** Pydantic schemas for request validation and response serialization. Must inherit from `app.common.serializers.BaseSchema`.
* **`selectors.py`:** **Read-only** database queries. No state modification, no commits!
* **`services.py`:** **Business logic and writes**. All `db.add()`, `db.commit()`, encryption, and worker enqueuing live here.
* **`views.py`:** Thin HTTP handlers that parse requests, call services/selectors, and return serialized responses.
* **`urls.py`:** `APIRouter` route definitions.

### Rule 8: Strict Type Annotations
* **Requirement:** All functions, methods, and parameters across the entire codebase must have complete Python type annotations (e.g., `def run_scan(url: str, timeout: int = 30) -> PillarScanResult:`).
* Use `Optional[T]`, `List[T]`, and `Dict[str, Any]` consistently.

### Rule 9: Async Database Session Lifecycle
* **Requirement:** All FastAPI handlers must use `db: AsyncSession = Depends(get_db)`.
* Do not keep long-running sessions open across network waits or Celery task dispatches. Use `await db.commit()` and `await db.refresh()` immediately after atomic writes.

### Rule 10: Sandboxed Evidence Storage Paths
* **Requirement:** All generated screenshots, HAR network traces, console logs, and exported PDF reports must be stored under the configured `STORAGE_DIR` (e.g. `storage/screenshots/`).
* Database records and API responses must store **relative paths** (e.g. `screenshots/run_123_case_1.png`), never hardcoded absolute machine paths.

---

## 3. Directory Layout Reference

When adding new files or features, place them strictly according to this tree:

```
├── app/
│   ├── core/                # System-wide infrastructure (config, db, security, middleware, templating)
│   ├── common/              # Shared base models, standard serializers, pagination, constants
│   ├── apps/                # Feature modules (accounts, websites, scan_profiles, test_packs, test_runs, reports, dashboard)
│   ├── templates/           # Shared Jinja2 templates (base.html, components/, errors/)
│   └── static/              # CSS design system, client-side JS
│
├── test_engine/             # Independent testing engine (NO web/DB dependencies)
│   ├── schema/              # YAML/JSON schema definitions, action/assertion models, validator
│   ├── policy/              # Allowlist validation, egress interception guard, risk checker
│   ├── browser/             # Playwright instance pool, isolated contexts, step runner, evidence
│   ├── pillars/             # The 4 quality pillars (functional, security, performance, accessibility)
│   └── scoring/             # Rubric weightings (0-100) and multi-pillar report aggregator
│
├── ai_engine/               # Assistive AI (generation & explanation)
│   ├── clients/             # Local offline Ollama client & LLM factory
│   ├── prompts/             # Test generation, boundary values, failure explainer
│   └── parsers/             # SchemaEnforcer for LLM responses
│
├── workers/                 # Celery task queue
│   ├── celery_app.py        # Celery broker configuration
│   └── tasks/               # run_scan.py, generate_ai_cases.py
│
├── storage/                 # Evidence repository (screenshots, traces, logs, exports)
└── tests/                   # Automated pytest suite (unit/ and integration/)
```

---

## 4. How to Add a New Test Step Action or Assertion

If you need to support a new browser action (e.g., `drag_and_drop`):
1. **Update Enum:** Add the new action to `test_engine.schema.actions.ActionType`.
2. **Update Runner:** Add the execution logic in `test_engine.browser.action_runner.ActionRunner.execute_step()`.
3. **Update Schema & Prompt:** Update the system prompt in `ai_engine.prompts.test_generation.SYSTEM_PROMPT` so the AI knows the new action is supported.
4. **Add Test:** Add a test case in `tests/unit/test_schema.py` verifying that YAML files with this action pass schema validation.

---

## 5. Verification Checklist Before Submitting Code

Before submitting any code changes, ensure all verification steps pass:

```bash
# 1. Activate virtual environment
source .venv/bin/activate

# 2. Run test suite
pytest -v

# 3. Check for schema and policy regressions
pytest tests/unit/test_schema.py tests/unit/test_policy.py
```
