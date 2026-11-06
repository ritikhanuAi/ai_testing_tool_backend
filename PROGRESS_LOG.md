# Engineering Progress Log & Step-by-Step Tracker

**Project:** AI Website Testing Platform (`ai_testing_tool_backend`)  
**Mission:** Build the **Functionality** and **UI/UX & Accessibility** testing pillars using text-based LLM assistance and deterministic browser checks. Screenshot-based VLM review is deferred.
**Pair Programming Mode:** User writes code; AI acts as Choreographer & Architect.  
**Current Date:** October 23, 2026  

---

## 🧭 Master Roadmap & Milestone Checklist

### 📍 Phase 1: AI Engine Infrastructure (`ai_engine/`)
- [x] **Step 1.1**: Inspect `ai_engine/clients/base.py` interface.
- [x] **Step 1.2**: Update `ai_engine/config.py` with STPI remote model settings (`AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL`).
- [x] **Step 1.3**: Implement `ai_engine/clients/remote.py` (High-speed Async HTTP client for STPI Qwen-27B).
- [x] **Step 1.4**: Wire `ai_engine/clients/factory.py` to support `stpi` / `remote`.
- [x] **Step 1.5**: Test live handshake from `ai_testing_tool_backend` to STPI GPU.

---

### 📍 Phase 2: Functionality Testing Tool (`test_engine/pillars/functional/`)
- [x] **Step 2.1**: Upgrade DOM & Form Spider (`spider.py`) to discover forms, inputs, buttons, and links.
- [x] **Step 2.2**: Build Multi-Scenario Semantic AI Test Generator (`ai_engine/prompts/test_generation.py`).
- [x] **Step 2.3**: Upgrade Playwright Action & Assertion Runner (`runner.py`) with screenshots on failure.
- [x] **Step 2.4**: Implement AI Failure Diagnosis & Fix Explainer (`ai_engine/prompts/failure_explainer.py`).
- [x] **Step 2.5**: Integrate Autonomous Functional Scan Pipeline.

---

### 📍 Phase 3: UI / UX & Accessibility Tool (`test_engine/pillars/accessibility/`)
- [x] **Step 3.1**: Verify & tune `axe-core` WCAG 2.1 AA scanner (`axe_runner.py`).
- [x] **Step 3.2**: Implement AI Generative HTML/CSS Code Fixer (`accessibility_fixer.py`).
- [x] **Step 3.3**: Implement VLM Screenshot UX Auditor (`ux_vision_scanner.py` & `ux_vision_evaluator.py`).
- [x] **Step 3.4**: Integrate Readability & Mobile Responsive Viewport checks (`responsive.py`, `readability.py`).
- [x] **Step 3.5**: Build Master `AccessibilityScanner` Runner and wire into `workers/tasks/run_scan.py`.

---

### 📍 Phase 4: Scoring & Final Report Integration
- [ ] **Step 4.1**: Standardize Findings Schema (`PillarFinding`) and 0–100 rubric scoring.
- [ ] **Step 4.2**: Generate unified report preview.

---

## 📝 Activity & Changelog

| Timestamp | Phase / Step | File(s) Modified | Summary of Milestone |
| :--- | :--- | :--- | :--- |
| 2026-10-23 | Setup | `AI_ENGINE_AND_PILLARS_BLUEPRINT.md` | Master plan approved and blueprint published. |
| 2026-10-23 | Setup | `PROGRESS_LOG.md` | Progress tracker established. |
| 2026-11-05 | Module testing | `AI_TESTING_GAPS_AND_ROADMAP.md`, `test_engine/pillars/functional/`, `workers/tasks/run_scan.py`, frontend module/report UI, `tests/unit/` | Implemented selected-page interaction matrices, sequential module control, per-case route resets, coverage reporting, contextual repeated-button factors, and checkpointed automatic continuation across case/time batches. Added focused automated coverage for discovery, counts/order, resume checkpoints, route boundaries, cancellation, and LLM-only mode. |
| 2026-11-05 | Report-driven interaction fix | `test_engine/browser/action_runner.py`, `test_engine/pillars/functional/`, `workers/tasks/run_scan.py` | Fixed row-dialog verification so a removed trigger does not cause a 30-second false failure; allow saved matrix batches to continue when dynamic controls are reported, while preserving incomplete coverage status for controls outside the manifest. |
| 2026-11-06 | Prioritized control coverage | `app/apps/test_runs/serializers.py`, `test_engine/pillars/functional/`, `workers/tasks/run_scan.py`, frontend module/report UI | Added user-configurable interaction priority order, prioritized first cases and action order, and staged discovery for safe triggers that reveal search fields or dropdowns. Newly revealed controls carry their opening action into each case; linked routes remain deferred until the selected page queue completes. |
| 2026-11-06 | Dropdown and popup follow-up | `test_engine/pillars/functional/combinatorial.py`, `test_engine/pillars/functional/spider.py`, frontend report UI | Separate selectable dropdown options from menu action buttons, verify that popup controls become visible, run setup clicks immediately before their priority tier, preserve priority tiers when permuting action order, and queue same-origin popup/navigation destinations after the current page completes. |
| 2026-11-05 | AI scope | `test_engine/pillars/accessibility/runner.py`, `workers/tasks/run_scan.py`, `AI_TESTING_GAPS_AND_ROADMAP.md` | Disabled screenshot-based VLM review during scans; text-based LLM test generation and accessibility fix suggestions remain available. Deferred visual regression and bounding-box work. |
