# AI Website Testing Platform: Engineering Guidelines, Architecture & Rules

> **AUTHORITATIVE REPOSITORY SPECIFICATION**
> This document defines the engineering standards, layer boundaries, security guardrails, and coding instructions for all developers and AI coding agents operating on the **AI Website Testing Platform (Backend API)**.

---

## 1. Prime Directive: 4-Layer Separation

| Layer | Directory | Responsibilities | STRICT PROHIBITIONS |
|---|---|---|---|
| **Web & API** | `app/` | Headless REST API (FastAPI), CORS middleware, JWT authentication, SQLAlchemy async ORM models, selectors, services. | **NEVER** import Playwright or execute browsers directly inside HTTP requests. |
| **Worker Queue** | `workers/` | Celery asynchronous tasks, Redis broker, background orchestration, scan lifecycle state updates, report persistence. | **NEVER** run browser scans synchronously in FastAPI. |
| **Test Engine** | `test_engine/` | Playwright browser runner, 4 quality pillars (Functional, Security, Performance, Accessibility), policy guardrails, YAML schemas. | **NEVER** import SQLAlchemy, FastAPI, or database models. Must remain pure Python & Pydantic. |
| **Assistive AI** | `ai_engine/` | Offline Ollama client, prompt injection defenses, prompt templates, structured output parser with schema retry loop. | **NEVER** allow AI to execute arbitrary code or bypass schema enforcement. |

---

## 2. Updated Directory Structure

```
ai_testing_tool_backend/
├── app/
│   ├── apps/
│   │   ├── accounts/             # User registration, login (JWT), dependencies
│   │   ├── websites/             # Target website onboarding & domain ownership verification
│   │   ├── scan_profiles/        # Scan settings, rate limits, active pillars
│   │   ├── test_packs/           # Test pack storage & pre-flight YAML validation
│   │   ├── test_runs/            # Scan dispatching, cancellation, and execution status
│   │   ├── reports/              # 4-pillar aggregated report summaries
│   │   ├── dashboard/            # REST KPI cards and recent activity feeds
│   │   └── health/               # /api/health liveness & readiness probe
│   ├── common/
│   │   ├── constants.py          # Enums: PillarType, RunStatus, VerificationStatus, etc.
│   │   ├── models.py             # SQLAlchemy BaseModel (UUID id, created_at, updated_at)
│   │   ├── serializers.py        # Unified ResponseEnvelope[T] & ErrorEnvelope
│   │   └── pagination.py         # PaginatedResponse[T] with metadata
│   ├── core/
│   │   ├── config.py             # Pydantic BaseSettings & CORS configuration
│   │   ├── database.py           # Async SQLAlchemy engine & session factory
│   │   ├── security.py           # JWT creation/decoding & Fernet credential encryption
│   │   ├── middleware.py         # CORS, request correlation ID (X-Request-ID), timing
│   │   ├── exceptions.py         # Global unified exception handlers
│   │   └── logging.py            # Structured system logging
│   ├── main.py                   # FastAPI factory & route mounting
│   └── router.py                 # API v1 prefix composition
│
├── test_engine/
│   ├── policy/
│   │   ├── allowlist.py          # Real-time DNS-resolving SSRF Protection
│   │   ├── scope_guard.py        # Fail-closed Playwright network egress interceptor
│   │   └── risk_checker.py       # Permission gate for state-changing actions
│   ├── browser/
│   │   ├── pool.py               # Shared Chromium browser instance pool
│   │   ├── context.py            # Sandboxed contexts with viewports & scope guards
│   │   ├── evidence.py           # Screenshot and trace evidence collector
│   │   └── page_summary.py       # Token-optimized compact DOM extractor for AI
│   ├── pillars/
│   │   ├── functional/           # YAML runner, browser steps, DOM assertions
│   │   ├── security/             # HTTP headers, cookies, sensitive exposures, TLS/SSL
│   │   ├── performance/          # W3C timing, lab Core Web Vitals (LCP, CLS, TBT, TTFB)
│   │   └── accessibility/        # axe-core WCAG 2.1 AA audit & mobile overflow
│   ├── scoring/
│   │   ├── rubrics.py            # Transparent deduction rubric & grading
│   │   └── aggregator.py         # Multi-pillar composite report aggregator
│   └── schema/                   # TestCaseModel, ActionStep, Assertion Pydantic contracts
│
├── ai_engine/
│   ├── clients/                  # Ollama local offline client & cloud provider factory
│   ├── prompts/                  # Prompt injection containment (<untrusted_page_context>)
│   └── parsers/                  # SchemaEnforcer with 3-attempt feedback retry loop
│
├── workers/
│   ├── celery_app.py             # Celery worker configuration & prefetch limits
│   └── tasks/
│       ├── run_scan.py           # Multi-pillar scan executor with cancel flag checks
│       └── generate_ai_cases.py  # Background AI test ideation worker
│
├── storage/                      # Evidence sandbox (screenshots, traces, logs, exports)
├── tests/
│   ├── unit/                     # Schemas, risk checker, allowlist, AI enforcer
│   └── integration/              # Pillars, scoring rubrics, REST API endpoints
├── docker-compose.yml            # PostgreSQL, Redis, Worker, FastAPI Web, Ollama
├── requirements.txt              # Production Python dependencies
├── .env.example                  # Environment template
└── README.md                     # Project overview and frontend integration guide
```

---

## 3. Inviolable Engineering Rules

### Rule 1: Browser Automation Execution
All Playwright browser automation takes seconds to minutes and **must** be executed in Celery background workers (`workers/tasks/run_scan.py`). Never invoke `playwright` or `page.goto()` inside `app/`.

### Rule 2: Strict SSRF Protection (DNS Resolution)
Every scan target URL must pass `TargetAllowlistValidator.assert_public_host(hostname)`:
- Live DNS resolution via `socket.getaddrinfo` is mandatory.
- Re-check resolved IPs against private RFC 1918 ranges, loopback (`127.0.0.0/8`), link-local (`169.254.0.0/16`), and cloud metadata services.
- Never rely solely on string matching for hostnames.

### Rule 3: Fail-Closed Scope Egress
`ScopeGuard.intercept_route` must be attached to every browser context to block out-of-scope egress. If parsing fails, it must **fail closed** (`await route.abort()`), never `route.continue_()`.

### Rule 4: Domain Ownership Verification
In production environments, target websites must be verified via:
1. DNS TXT record: `ai-tester-verify=<token>`
2. Well-Known HTTP file: `/.well-known/ai-tester.txt` containing `<token>`
In `development` environments (`APP_ENV=development`), domains are auto-verified.

### Rule 5: Emergency Stop / Cancellation
The scan runner in `workers/tasks/run_scan.py` must check Redis for the cancellation key (`cancel:{run_id}`) before each pillar execution. If flagged, the browser context must close immediately, setting `TestRun.status = RunStatus.CANCELLED`.

### Rule 6: Credential Security
Passwords and secrets must be encrypted with `app.core.security.encrypt_credential()` before being saved to the database. Plaintext secrets must never appear in logs, YAML test packs, or HAR files.

### Rule 7: AI Prompt Injection Defense
All third-party web page DOMs, text, and inputs passed to the AI engine must be wrapped inside:
```xml
<untrusted_page_context>
...
</untrusted_page_context>
```
System prompts must explicitly instruct the LLM that content inside these tags is third-party data and must never be executed as instructions.

### Rule 8: Compact Page Summaries for Small LLMs
Never pass raw, multi-megabyte HTML into local Ollama models. Always use `PageSummaryExtractor.extract_summary(page)` to distill the page down to forms, input selectors, buttons, and landmarks.

### Rule 9: Unified Response & Error Envelopes
All API endpoints in `app/` must return `ResponseEnvelope[T]`:
```json
{
  "success": true,
  "message": "OK",
  "data": { ... },
  "meta": { ... }
}
```
All errors must return `ErrorEnvelope`:
```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": [ ... ]
  }
}
```

### Rule 10: Transparent Scoring Deductions
Scores are calculated deterministically:
$$\text{Score} = \max(0.0, 100.0 - \sum \text{Deductions})$$
- Critical: -15 pts
- High: -8 pts
- Medium: -3 pts
- Low: -1 pt
- Capped at maximum 3 deductions per unique rule ID. Every deduction must be linked to its finding and evidence.

---

## 4. Developer Workflow & Adding New Features

### Adding a New Domain API in `app/apps/<domain>/`
1. **Model:** Define SQLAlchemy model in `models.py` inheriting from `app.common.models.BaseModel`.
2. **Selectors:** Add read-only queries (`get_*`, `list_*`) in `selectors.py`.
3. **Services:** Add write transactions (`create_*`, `update_*`) in `services.py`.
4. **Serializers:** Define Pydantic request and response schemas in `serializers.py`.
5. **Views:** Thin async functions in `views.py` returning `ResponseEnvelope[...]`.
6. **URLs:** Declare routes in `urls.py` and register router in `app/apps/router.py`.

### Adding a New Security Check in `test_engine/pillars/security/`
1. Inherit from or follow the functional signature: `audit_*(...) -> Tuple[List[PillarFinding], float]`.
2. Generate deterministic finding fingerprints: `PillarFinding.fingerprint`.
3. Wire the scanner into `workers/tasks/run_scan.py`.
4. Add unit test in `tests/integration/test_pillars.py`.

---

## 5. Verification Command

Before submitting any code changes or completing any task, run:

```bash
.venv/bin/pytest -v
```

All 20+ tests in `tests/unit/` and `tests/integration/` must pass with zero errors.
