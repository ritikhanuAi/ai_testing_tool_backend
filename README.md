# AI Website Testing Platform - Backend API

A deterministic, multi-pillar automated website testing platform backend combining Playwright browser execution with assistive AI test case ideation, plain-language failure diagnostics, and a 100% headless RESTful API designed for modern frontends (**Next.js**, **React**, or **Vue**).

Built in accordance with the specifications in `AI_Website_Testing_Platform_Project_Proposal.docx` and enterprise architectural standards.

---

## 📚 Essential Developer Documentation

- **[Developer Handbook (DEVELOPER_GUIDE.md)](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/DEVELOPER_GUIDE.md):** Complete step-by-step tutorial on how to build new API endpoints and develop AI engine capabilities (local Ollama + cloud LLMs).
- **[Repository Guidelines & Rules (PROJECT_GUIDELINES.md)](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/PROJECT_GUIDELINES.md):** Authoritative rules, 4-layer boundaries, and security policies.
- **[Architectural Blueprint (ARCHITECTURE.md)](file:///home/hanuai/Documents/Office%20work%20projects/Ai_testing_tool/ARCHITECTURE.md):** Deep-dive into data models, workers, and scanning flows.

---

## The Four Quality Pillars

The platform evaluates web applications across four core quality dimensions:

1. **Functional Testing:** Authored YAML/JSON test packs, deterministic DOM assertions, route health, browser failures, and a deterministic interaction matrix when no test pack is supplied. The matrix enumerates the full Cartesian product of discovered safe controls and configured finite text/search values. Optional action-order permutations multiply the manifest by N!. Read-only is the default; risky state-changing actions require an enabled profile.
2. **Security Testing:** Checks response headers/CSP, cookie flags, mixed content, insecure forms/password transport, server errors, TLS certificates and legacy protocols, and a short list of exposed files/endpoints.
3. **Performance Testing:** Uses browser timing observers and resource measurements on crawled pages, reports large-resource findings and page-sample p75 values.
4. **UX & Accessibility Testing:** Runs axe-core WCAG checks, responsive overflow, and missing image alt-text checks on crawled routes. Text-based LLM fix suggestions are available; screenshot-based VLM review is disabled for the current LLM-only phase.

### Site Crawl and Grade Scope

Scans are queued by `app/apps/test_runs/services.py` and run in the Celery worker (`workers/tasks/run_scan.py`) in an isolated Playwright browser context. Before a scan is queued, the target must be verified/authorized, match the website's allowlist, and resolve only to public IP addresses. The browser `ScopeGuard` checks each request, blocks private/out-of-scope hosts and unsupported ports, and applies the profile's request-rate limit.

`test_engine/crawl/site_crawler.py` performs a bounded breadth-first crawl of anchor links in explicitly approved domains. Sitemap URLs are opt-in (`include_sitemap_urls`). The module wizard defaults to depth 0 (only the selected page); linked-page traversal is an explicit depth setting. Each page waits for common loading overlays to clear, then its full deterministic case queue runs before the crawler opens a linked page. If the queue is incomplete, linked-page traversal stops. Defaults are up to 100 page visits, depth 5, 500 links per page, at most 5 sitemap documents/1,000 sitemap URLs when sitemap crawling is enabled, and a bounded scan timeout. The crawler skips paths/query actions that look destructive (such as logout, delete, checkout, or payment); it does not submit forms. Private, reserved, unresolved, and out-of-scope hosts are blocked.

The report records visited/discovered page counts and whether page, depth, link, sitemap, unsafe-route, loading-state, case-count, or time limits truncated coverage. A warning status means the grade covers only the inspected sample. Link crawling discovers anchor routes; routes hidden behind app interactions or login flows may not be discoverable. Functional route health, security, performance, and deterministic accessibility checks use the crawled pages. The exhaustive matrix enumerates every combination in the finite domains it discovered: native select options, custom listbox options exposed with `role=option`, safe custom menu items, checkbox states, radio choices, tabs, discrete slider steps, safe filter/search buttons, and configured values for visible text/search controls. Empty input is always included. Action-order permutations are optional and multiply the count by N!. A case ceiling prevents unbounded runs; when the ceiling or timeout prevents completion, planned/executed/pending counts are reported and the run is marked incomplete. Unsupported, inaccessible, or newly exposed conditional control domains are called out as incomplete. This is exhaustive only over the discovered controls and configured finite values, not every hidden route, arbitrary free-text string, or application business state. Authored test packs run as specified, while AI-generated interactions stay on the starting page unless linked-page depth is enabled. Screenshot-based VLM review is disabled during scans; text-based LLM test generation and accessibility fix suggestions remain available. Security file/TLS probes are limited to ten origins. If a TLS handshake cannot be completed, the report marks TLS as unverified and warns.

The module wizard starts with synthetic text values `sample` and `__no_matching_result__`, a per-page case ceiling of 1,000, and action-order permutations off. The user can change those values, raise the ceiling to 100,000, select multiple modules, enable linked-page depth, and stop the active run. Repeated same-name safe buttons (such as one row action per record) are grouped as alternatives in one factor. When action-order permutations are enabled, only actions selected in a case are permuted; untouched controls do not multiply the order count. If the matrix exceeds the case ceiling, the scanner executes the deterministic prefix up to that ceiling and reports the exact remainder as pending. The crawler reserves 15% of the scan timeout for the remaining pillars and report persistence; if interaction cases remain when the matrix budget ends, the report lists them as pending.

### Test Case Modes: Human or AI (never both)

Each scan runs in exactly one mode:

- **Human mode** — a human-written test pack (YAML/JSON) is supplied. Only those cases run, and **no AI is used anywhere in the scan**: no AI test writing or exploration, no auto-generated form checks, no AI failure explanations, no AI accessibility fix suggestions (built-in templates are used), and no AI screenshot review. The accessibility score is then computed from WCAG, mobile and readability checks only (renormalized), and the report records `test_case_mode: "human"` and `ai_used: false`.
  Human test cases can also check attributes (`attribute_equals`), absent text (`text_not_contains`), and lab timings: `page_load_time_less_than` (Navigation Timing load), `response_time_less_than` (from the last non-wait step until the network is idle, minus Playwright's 500 ms idle window), and `render_time_less_than` (Largest Contentful Paint, or First Contentful Paint when LCP is unavailable). Pillar names `functionality` and `user_friendly_ui` are accepted as `functional` and `accessibility`. See `TEST_CASE_JSON_GUIDE.md`.
- **AI mode** — no test pack. By default, deterministic exhaustive interaction combinations run; natural-language AI goals and generated form checks are used only in the bounded exploratory mode when exhaustive combinations are turned off. Text-based LLM explanations and fix suggestions remain available; screenshot-based VLM review is off.

### What the Pillars Measure

- **Functional:** HTTP status and navigation failures for each visited route, JavaScript/console errors, failed first-party requests, broken images, plus authored or AI-guided starting-page interactions. Authored journeys run from the starting page and any explicit test-pack navigation.
- **Security:** CSP and common security headers (including HSTS, frame, MIME, referrer, and permissions policies), cookie Secure/HttpOnly/SameSite and prefix rules, mixed content, HTTP form/password transport, server errors, and TLS certificate expiry/legacy TLS 1.0–1.1 support. It makes bounded GET checks for `.env`, `.git/HEAD`, `phpinfo.php`, and `actuator/health`; response bodies are sampled only to confirm expected content and are not included in findings.
- **Performance:** Browser lab metrics include FCP, LCP, CLS, observed INP, TTFB, DOMContentLoaded/load timing, long-task blocking time, resource count/type, transfer/encoded sizes, and oversized assets. The performance runner reports per-metric page-sample p75 values and sample counts.
- **Accessibility/UX:** axe-core 4.9.1 WCAG 2.0 A/AA checks, 375px responsive overflow, and missing image alt-text checks on successfully crawled routes. Text-based LLM axe fixes are limited to the first page successfully audited. Screenshots may be retained as scan evidence but are not sent to a vision model during scans.

The implementation uses Playwright browser APIs for crawl and UI observations, axe-core for automated WCAG rules, Python `ssl`/`socket` for TLS and public-DNS checks, HTTPX for bounded exposure checks, and the existing Celery/Redis queue for asynchronous execution and cancellation. Text LLM calls use the configured LLM provider for test generation, failure explanations, and accessibility fix suggestions. The VLM client and screenshot reviewer remain in the codebase but are not called during scans. This project does not currently use Lighthouse, real-user monitoring, or a full OWASP active exploit suite.

Performance values are browser-lab samples, not real-user field data. INP is shown as unmeasured when the scan observes no qualifying real-user interaction; the synthetic matrix is not an INP measurement. Resource size metrics also show how many browser entries were unavailable due to cross-origin timing restrictions. Long-task blocking time is an observed-load approximation, not a Lighthouse score.

The overall score uses the proposal's pillar weights (functional 35%, security 25%, performance 20%, accessibility 20%), renormalized over pillars that ran. The report includes the effective weights. Pillar scores use severity-based deductions, capped at three occurrences per rule. Letter grades are A (90+), B (80–89.9), C (70–79.9), D (60–69.9), and F (under 60). Disabled pillars are marked `SKIPPED`, never treated as perfect scores. A report passes only when its overall score is at least 70, it has no critical findings, and every enabled pillar is `PASSED`. A pillar with no usable observations is marked `FAILED` with a zero score and an explicit coverage note.

---

## Architecture Overview (Headless REST)

```
ai_testing_tool_backend/
├── app/                     # FastAPI Headless REST API
│   ├── core/                # Config, database (SQLAlchemy async), security (JWT/Fernet), middleware, exceptions
│   ├── common/              # ResponseEnvelope, pagination, base schemas, constants
│   └── apps/                # REST domain modules (accounts, websites, scan_profiles, test_packs, test_runs, reports, dashboard, health)
│
├── test_engine/             # Independent Browser & Scanning Workhorse (No Web/DB dependencies)
│   ├── schema/              # Pydantic test case schema, actions, assertions, pre-flight validator
│   ├── policy/              # Scope allowlist, egress guardrails, write-action permission gates
│   ├── browser/             # Playwright browser pool, isolated contexts, action/assertion runners, screenshots
│   ├── crawl/               # Bounded sitemap/anchor discovery and redaction helpers
│   ├── pillars/             # The 4 quality pillars (Functional, Security, Performance, Accessibility)
│   └── scoring/             # Rubrics, weighted calculations, report aggregation
│
├── ai_engine/               # Assistive AI (Generation & Explanation)
│   ├── clients/             # Local offline Ollama client & cloud provider factory
│   ├── prompts/             # Test generation, boundary inputs, failure explainer
│   └── parsers/             # Strict schema enforcer (guarantees valid test cases from LLM)
│
├── workers/                 # Asynchronous Task Queue (Celery + Redis)
│   ├── celery_app.py        # Worker configuration & prefetch controls
│   └── tasks/               # Isolated browser scan coordinator, background AI generation, report persistence
│
└── storage/                 # Evidence repository for screenshots, HARs, traces, and exports
```

---

## API Design & Frontend Integration

This backend is completely decoupled from UI templates and exposes a structured JSON API:

### 1. Unified Response Envelope
Every successful endpoint returns a standardized response envelope:
```json
{
  "success": true,
  "message": "OK",
  "data": { ... },
  "meta": { "total": 100, "page": 1, "page_size": 20 }
}
```

### 2. Unified Error Envelope
All domain, HTTP, and Pydantic validation errors return a consistent error structure:
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid request payload format or parameters",
    "details": [
      { "field": "body -> email", "message": "value is not a valid email address" }
    ]
  }
}
```

### 3. Key Endpoints for Frontend Clients

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | API service discovery and docs links |
| `GET` | `/api/health` | System health probe (PostgreSQL, Redis, Ollama) |
| `GET` | `/api/v1/dashboard/summary` | KPI cards (total websites, total runs, pass rate, score) |
| `GET` | `/api/v1/dashboard/recent-activity` | Recent scan execution feed |
| `POST` | `/api/v1/accounts/register` | Register new user |
| `POST` | `/api/v1/accounts/login` | JWT login token |
| `GET` | `/api/v1/accounts/me` | Current authenticated user profile |
| `GET` | `/api/v1/websites` | List registered websites |
| `POST` | `/api/v1/websites` | Register new target website |
| `GET` | `/api/v1/scan-profiles` | List scan profiles |
| `GET` | `/api/v1/test-packs` | List test packs |
| `POST` | `/api/v1/test-packs/validate` | Pre-flight YAML syntax check |
| `POST` | `/api/v1/test-runs` | Trigger automated 4-pillar scan |
| `GET` | `/api/v1/test-runs/{run_id}` | Poll test run progress status |
| `GET` | `/api/v1/reports/{run_id}` | Retrieve comprehensive 4-pillar report |
| `GET` | `/storage/screenshots/{filename}` | Retrieve screenshot evidence |

Interactive Swagger documentation is available at `http://localhost:8000/docs` and ReDoc at `http://localhost:8000/redoc`.

### 4. Connecting Next.js or React.js
Set `CORS_ALLOWED_ORIGINS` in your `.env` to include your frontend dev and prod URLs:
```dotenv
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
```
In your frontend application:
- **Base URL:** `http://localhost:8000/api/v1`
- **Auth Header:** `Authorization: Bearer <access_token>`
- **TypeScript Types:** Generate types directly from the backend's OpenAPI schema using `npx openapi-typescript http://localhost:8000/openapi.json -o types/api.d.ts`.

---

## Quickstart Guide

### 1. Running with Docker Compose (Recommended)
Spawns PostgreSQL, Redis, Celery Worker (with Playwright browsers installed), the FastAPI Web server, and Ollama (available for other local AI uses):

```bash
# Copy environment variables
cp .env.example .env

# Launch all services
docker-compose up --build

# Screenshot review uses REMOTE_LLM_BASE_URL / REMOTE_LLM_API_KEY / REMOTE_LLM_MODEL from .env.
# The configured remote model must accept image input through the chat-completions-compatible API.
```

Access the OpenAPI Swagger Explorer at `http://localhost:8000/docs`.

### 2. Running Locally (Development Mode)

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright browsers
playwright install --with-deps chromium

# 4. Start Redis and PostgreSQL (or use docker-compose for dependencies only):
docker-compose up -d db redis

# 5. Run Celery Worker
celery -A workers.celery_app worker --loglevel=info --concurrency=2

# 6. Run FastAPI Application
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Running the Automated Test Suite

Run unit and integration tests across schemas, policy guards, pillars, and API endpoints:

```bash
.venv/bin/pytest -v
```
