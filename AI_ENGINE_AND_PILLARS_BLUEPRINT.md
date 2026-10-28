# Master Architecture Blueprint: AI Engine & Core Testing Pillars

**Project:** AI-Powered Universal Website Tester  
**Scope of Work:** AI Engine Infrastructure, Functionality Pillar, and UI/UX & Accessibility Pillar  
**Target Directory:** `ai_testing_tool_backend`  
**AI Inference Backend:** Remote STPI NVIDIA Tesla V100S GPU (32GB VRAM) running Unsloth Qwen-27B VLM (OpenAI-Compatible `/v1` endpoint) with Ollama fallback  

---

## 1. Executive Summary & Hackathon Alignment

The Hackathon Brief establishes five strict evaluation criteria:
1. **Issues Found (30%)**: Real, verified bugs discovered on arbitrary websites with near-zero false positives.
2. **Use of AI (25%)**: AI performs deep, meaningful engineering tasks (test scenario generation, screenshot layout inspection, failure diagnosis), not superficial text summarization.
3. **Report Quality (20%)**: Precise severity, locator/selector identification, and actionable developer fixes.
4. **Stack Independence (15%)**: Operates as a pure black-box tester given only a URL (no source code or configuration).
5. **Demo and Usability (10%)**: Clean, resilient execution.

Our mission is to build the most advanced **Functionality** and **UI/UX & Accessibility** pillars in the competition by uniting:
- **Headless Playwright Automation**: Real DOM interaction, form filling, button clicking, and screenshot capture.
- **27-Billion Parameter Multimodal AI**: High-speed remote GPU inference (~55–62 tokens/sec) providing semantic test case synthesis, visual UX auditing, and automated code repair.

---

## 2. End-to-End System Architecture

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        UNIVERSAL AI TESTING PLATFORM                                   │
└───────────────────────────────────┬────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               AI ENGINE (ai_engine/)                                   │
│  ┌─────────────────────────┐  ┌─────────────────────────┐  ┌────────────────────────┐  │
│  │   LLMClientFactory      │  │  OpenAICompatibleClient │  │      OllamaClient      │  │
│  │  (Dynamic Provider)     │  │  (STPI Tesla V100 GPU)  │  │   (Local Fallback)     │  │
│  └────────────┬────────────┘  └────────────┬────────────┘  └───────────┬────────────┘  │
│               │                            │                           │               │
│               └────────────────────────────┼───────────────────────────┘               │
│                                            ▼                                           │
│                       Structured JSON & Vision Parser                                  │
│                 (Pydantic Schema Enforcer + Base64 Projector)                          │
└───────────────────────────────────┬────────────────────────────────────────────────────┘
                                    │
         ┌──────────────────────────┴──────────────────────────┐
         ▼                                                     ▼
┌───────────────────────────────────┐ ┌──────────────────────────────────────────────────┐
│  PILLAR 1: FUNCTIONALITY          │ │  PILLAR 2: UI / UX & ACCESSIBILITY               │
│  (test_engine/pillars/functional) │ │  (test_engine/pillars/accessibility)             │
├───────────────────────────────────┤ ├──────────────────────────────────────────────────┤
│ 1. Deep Spider & DOM Discovery    │ │ 1. Deterministic WCAG 2.1 AA (axe-core)          │
│    • Inputs, forms, buttons, links│ │    • Contrast, missing alt, ARIA hierarchy       │
│    • Console error listeners      │ │                                                  │
│                                   │ │ 2. AI Code Fix Generator                         │
│ 2. Semantic Test Case Generator   │ │    • Produces exact corrected HTML/CSS snippets  │
│    • Empty / required validation  │ │                                                  │
│    • Boundary & overflow test     │ │ 3. 🌟 Multimodal VLM Screenshot Review           │
│    • Type mismatch test           │ │    • Desktop (1280px) & Mobile (375px) audits    │
│    • Safe XSS & SQL canaries      │ │    • Detects text overflows, overlapping cards,  │
│                                   │ │      cramped elements & confusing copy/UX        │
│ 3. Playwright Action Runner       │ │                                                  │
│    • Executes steps, verifies DOM │ │ 4. Readability & Responsive Scanners             │
│                                   │ │    • Reading age/clarity & mobile layout checks  │
│ 4. AI Root-Cause Failure Analyzer │ └──────────────────────────────────────────────────┘
│    • Diagnoses errors & code fixes│
└───────────────────────────────────┘
```

---

## 3. Pillar 1: The Functionality Testing Tool (Execution Blueprint)

The Functionality Tool transforms the scanner into an autonomous QA engineer capable of testing any arbitrary website end-to-end.

### 3.1. Deep DOM & Form Discovery (`spider.py`)
- Discovers all internal navigation links (depth 1–3).
- Extracts all interactive elements on the page:
  - `<form>` structures, associated `<input>`, `<textarea>`, `<select>`, `<button>`.
  - Buttons with active event handlers or navigation anchors.
  - Interactive carousels, tabs, and navigation dropdowns.
- Attaches listeners for unhandled JavaScript exceptions (`page.on('pageerror')`) and broken HTTP assets (`404` / `500` network responses).

### 3.2. Semantic AI Test Case Generator (`test_generation.py`)
Instead of static or dummy tests, the AI inspects the page metadata and form schemas to generate 5 targeted test scenarios matching the `TestCaseModel` schema:
1. **Empty / Required Fields Test**: Submits forms with empty inputs to verify client-side validation triggers and prevents illegal submission.
2. **Type & Format Validation**: Injects invalid emails (`invalid-email`), non-numeric phone values, or negative numbers to check error bounds.
3. **Boundary & Buffer Overflow**: Tests long boundary strings (>500 chars) and Unicode symbols to ensure the server and UI don't crash.
4. **Safe Security Canaries (Non-Destructive)**: Tests harmless canaries (`<script>alert(1)</script>`, `' OR '1'='1'`) to verify input sanitization and XSS reflection prevention without damaging data.
5. **Happy Path Flow**: Uses sensible mock data inferred from field names (e.g., `test@example.com`, `John Doe`) to verify valid submission flows.

### 3.3. Autonomous Playwright Action Runner (`runner.py`)
- Executes the generated test steps sequentially (`open_page`, `type_text`, `click`, `select_option`, `wait`).
- Verifies assertions (`element_visible`, `text_contains`, `url_contains`, `element_hidden`).
- Takes timestamped screenshot evidence whenever a test assertion or action fails.

### 3.4. AI Failure Diagnosis & Fix Recommender (`failure_explainer.py`)
- When a functional step fails, the AI analyzes:
  - The failed action and target selector.
  - The DOM state at the moment of failure.
  - Browser console logs and HTTP response status.
- The AI outputs:
  - **Root Cause Analysis**: Why the failure occurred (e.g., race condition, missing form validation, unhandled 500 error).
  - **Severity Rating**: `CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`.
  - **Actionable Developer Fix**: Concrete code or configuration fix.

---

## 4. Pillar 2: UI / UX & Accessibility Tool (Execution Blueprint)

While other teams rely solely on rule-based linters, our solution implements a **dual-layer deterministic + generative vision system**:

### 4.1. Layer 1: Deterministic WCAG 2.1 AA/AAA Audit (`axe_runner.py`)
- Injects `axe-core` directly into the live Playwright page.
- Audits full WCAG 2.1 rules:
  - Color contrast violations (`color-contrast`).
  - Missing image alternative text (`image-alt`).
  - Form input labels (`label`).
  - ARIA attributes and landmark hierarchy.
- Computes standard penalty deductions and maps violations into `PillarFinding` objects.

### 4.2. Layer 2: AI Code-Fix Generator (`accessibility_fixer.py`)
- For every violation detected by `axe-core`, the offending HTML node is passed to the AI.
- The AI generates the **exact corrected HTML/CSS snippet** (e.g., adding `aria-label`, correcting color contrast values, or wrapping in proper `<label>` elements).

### 4.3. Layer 3: Multimodal VLM Screenshot Review (`ux_vision_scanner.py` & `ux_vision_evaluator.py`)
*This fulfills the brief's explicit requirement: "review screenshots for UX: confusing layout, unclear error messages."*
- Playwright captures high-resolution screenshots at two critical viewports:
  1. **Desktop Viewport**: 1280 × 720
  2. **Mobile Viewport**: 375 × 667
- The screenshots are Base64-encoded and transmitted to our **remote STPI Tesla V100 GPU running Qwen-27B Vision Model**.
- The Vision Model audits:
  - **Layout & Visual Anomalies**: Text overflowing outside card boundaries, misaligned buttons, horizontal scrollbars on mobile.
  - **UX Confusion & Dark Patterns**: Deceptive call-to-action buttons, unreadable banner overlays, modal traps that cannot be dismissed.
  - **Clarity of Error Messages**: Confusing, cryptic, or absent validation feedback.

---

## 5. Step-by-Step Implementation Roadmap

We will implement this in a structured, phased order:

### Step 1: Remote STPI AI Engine Client Setup
- Create `ai_engine/clients/openai_compat.py` (Supports Bearer auth, custom base URLs, text generation, and vision image payloads).
- Update `ai_engine/clients/factory.py` to support `AI_PROVIDER=stpi` or `AI_PROVIDER=openai_compat`.
- Update `ai_engine/config.py` to include base URL, API key, and model names.

### Step 2: Build the Functionality Tool (Our First Focus)
1. **Spider & Extractor**: Enhance `test_engine/pillars/functional/spider.py` to extract forms, inputs, buttons, and links.
2. **AI Test Generator**: Update `ai_engine/prompts/test_generation.py` to produce structured multi-scenario test cases.
3. **Action & Assertion Execution**: Verify and optimize `test_engine/pillars/functional/runner.py`.
4. **AI Failure Explainer**: Integrate `ai_engine/prompts/failure_explainer.py` to generate diagnoses for failed tests.
5. **Standalone Functional Test Run**: Execute against an approved practice app (e.g., `http://zero.webappsecurity.com` or local test server).

### Step 3: Build the UI / UX & Accessibility Tool
1. **Axe-Core Integration**: Verify `test_engine/pillars/accessibility/axe_runner.py`.
2. **AI Fix Generator**: Implement `ai_engine/prompts/accessibility_fixer.py`.
3. **VLM Screenshot Review**: Implement `ux_vision_scanner.py` and `ux_vision_evaluator.py` to connect Playwright viewport captures with the STPI VLM.
4. **Scoring & Reporting**: Aggregate WCAG + UX Vision findings into the final accessibility score.

---

## 6. Verification & Quality Gates

Each milestone will be verified against the following quality gates:
- **100% Schema Validity**: All AI outputs conform strictly to Pydantic models.
- **Resilience**: If the AI model or a web element fails, the scanner degrades gracefully without crashing the scan run.
- **Speed**: Full functional & accessibility scan completes in under 60 seconds per page.
