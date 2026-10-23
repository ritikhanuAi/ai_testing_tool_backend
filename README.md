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

1. **Functional Testing:** Human-authored YAML/JSON test packs (`SITE-FUNC-001`), interactive browser steps (`click`, `type`, `select_option`, `wait`), deterministic DOM assertions, and uncaught browser console crash detection.
2. **Security Testing:** Passive and low-risk checks auditing HTTP response headers (CSP, HSTS, X-Frame-Options), cookie security flags (`HttpOnly`, `Secure`, `SameSite`), and common exposed sensitive endpoints (`.git`, `.env`).
3. **Performance Testing:** W3C Navigation Timing (TTFB, DOMContentLoaded), Core Web Vitals (LCP, CLS, FCP), and asset payload weight analysis.
4. **UX & Accessibility Testing:** Automated WCAG 2.1 AA audits via `axe-core`, mobile viewport responsiveness checks (detecting horizontal overflow on 375px screens), and missing media `alt` tags.

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
Spawns PostgreSQL, Redis, Celery Worker (with Playwright browsers installed), the FastAPI Web server, and Ollama:

```bash
# Copy environment variables
cp .env.example .env

# Launch all services
docker-compose up --build
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
