# AI Testing Engine: Comprehensive Gaps Analysis & Next-Gen Roadmap

**Document:** AI Testing Engine Assessment & Roadmap  
**Target Repository:** `ai_testing_tool_backend`  
**AI Stack:** Remote STPI Qwen-27B VLM (OpenAI-Compatible) + Offline Ollama Fallback + Playwright  
**Date:** October 2026  
**Status:** Approved Architecture Blueprint  

---

## 1. Executive Assessment & Maturity Score

| Dimension | Current Rating (1–5) | Current State | Target State (Production Ready) |
|---|:---:|---|---|
| **AI Connectivity & Schema Enforcement** | ⭐⭐⭐⭐ (4/5) | High-speed async client, Pydantic retry feedback loop. | Multi-model fallback, streaming response parsing. |
| **Interactive Element Extraction** | ⭐⭐ (2/5) | Scrapes raw button text and only standard `<form>` tags. | Accessibility Tree distillation with unique DOM selectors. |
| **Test Suite Coverage** | ⭐⭐ (2/5) | Hardcoded to return only 1 test case. | Multi-scenario test suite (3–8 diverse journeys). |
| **Locator Reliability** | ⭐ (1/5) | AI hallucinates CSS selectors not guaranteed to exist. | Verified DOM locators, fuzzy matching, self-healing. |
| **SPA & Framework Support** | ⭐⭐ (2/5) | Blind to `<div>` buttons, dialogs, modern React/Vue inputs. | Framework-agnostic interactive widget recognition. |
| **Multimodal Vision (VLM)** | ⭐⭐⭐ (3/5) | Basic desktop screenshot UX review. | Mobile/desktop visual regression + bug bounding boxes. |
| **Agent Autonomy** | ⭐⭐ (2/5) | Static pre-planned scripts (open ➔ type ➔ click). | Dynamic ReAct loop (Observe ➔ Act ➔ Observe). |

---

## 2. Deep Dive: Current AI Architecture Gaps

### 🔴 Gap 1: "Selector Hallucination" (Highest Functional Risk)
* **Location in Code:** `workers/tasks/run_scan.py` (`_discover_and_generate_ai_tests`) & `ai_engine/prompts/test_generation.py`
* **The Problem:** The spider currently extracts only plain text button labels and input names:
  ```python
  page_desc = f"Interactive buttons: {', '.join(landmarks.get('buttons'))}"
  ```
  It feeds the LLM text like `["Login", "Shop", "Submit"]` **without** actual DOM locators (IDs, classes, XPath, test-ids, or coordinates).
* **Failure Impact:** The LLM is forced to guess selectors (e.g., `#login-btn` or `button[type="submit"]`). On modern web apps with dynamic CSS (Tailwind, CSS Modules, Styled Components), Playwright fails with:
  ```text
  TimeoutError: locator.click: Timeout 30000ms waiting for locator('#login-btn')
  ```
* **Required Fix:** Extract an **Interactive Element Catalog** mapping human labels to stable Playwright selectors before sending context to the AI.

---

### 🔴 Gap 2: Single Test Case Bottleneck (Suite Deficit)
* **Location in Code:** `workers/tasks/run_scan.py:157`
* **The Problem:** The code hardcodes returning a single test case:
  ```python
  valid, case_model, _ = SchemaEnforcer.parse_test_case(raw_response)
  if valid and case_model:
      return [case_model]
  ```
* **Failure Impact:** Even though `options.max_ai_cases` is configured to `5`, only 1 test case is generated. Testing a single happy path leaves search, navigation, validation, and error states completely untested.
* **Required Fix:** Prompt the AI to generate a structured `TestSuiteModel` containing 3–5 distinct user journey scenarios.

---

### 🔴 Gap 3: Form-Centric Blindspot (SPAs & Modern Web Apps)
* **Location in Code:** `test_engine/pillars/functional/spider.py` (`extract_forms`)
* **The Problem:** The crawler relies strictly on standard HTML `<form>` tags:
  ```javascript
  const forms = document.querySelectorAll('form');
  ```
* **Failure Impact:** Over 70% of modern web apps (React, Next.js, Vue, Svelte) do not use `<form>` tags. They use `<div>` containers with controlled React state, modal sheets, and `button onClick`.
* **Result:** On websites like Apple.com, modern dashboards, or search interfaces, `extract_forms` returns empty (`[]`), causing the AI to generate **zero** form tests.
* **Required Fix:** Implement a widget-level crawler that discovers interactive clusters (`role="dialog"`, search inputs, filter bars, card buttons) regardless of `<form>` tags.

---

### 🔴 Gap 4: Static Script Generation vs. Interactive ReAct Agent
* **Location in Code:** `ai_engine/prompts/test_generation.py`
* **The Problem:** The AI plans all actions (open ➔ type ➔ click ➔ assert) in a single upfront prompt before touching the page.
* **Failure Impact:** If Step 2 clicks a menu or opens a modal dialog, the page DOM changes. If the AI planned Step 3 before observing the open modal, Step 3 attempts to click elements that don't exist yet, causing premature test failure.
* **Required Fix:** Introduce an autonomous **ReAct (Reason + Act)** agent loop:
  $$\text{Page State } S_0 \xrightarrow{\text{Action } A_0} \text{New State } S_1 \xrightarrow{\text{AI Re-eval}} \text{Action } A_1$$

---

### 🔴 Gap 5: Weak & Surface-Level Assertions
* **Location in Code:** `ai_engine/prompts/test_generation.py` & `test_engine/pillars/functional/runner.py`
* **The Problem:** The generated assertions are trivial fallback checks like:
  ```json
  {"type": "url_contains", "value": "example.com"}
  ```
* **Failure Impact:** The test "passes" even if the website displayed an internal server error or a broken form, because the URL did not change.
* **Required Fix:** Teach the AI to generate semantic assertions:
  * "Assert error message container `.error-msg` is visible on invalid submission"
  * "Assert results grid `.search-results` contains at least 1 card"
  * "Assert URL redirects to dashboard or success state"

---

### 🔴 Gap 6: Multimodal Vision (VLM) is Underutilized
* **Location in Code:** `test_engine/pillars/accessibility/ux_vision_scanner.py`
* **The Problem:** The vision model is currently only fed a single static desktop screenshot to find generic text overlap.
* **Missed Opportunities:**
  * **Visual Regression:** No comparison between before-click and after-click states.
  * **Responsive Visual Mismatch:** Does not inspect tablet vs. mobile layouts side-by-side.
  * **Visual Coordinate Highlighting:** Does not output pixel bounding boxes `[ymin, xmin, ymax, xmax]` to highlight bugs on the screenshot in the frontend UI.

---

### 🔴 Gap 7: Lack of Self-Healing Test Execution
* **Location in Code:** `test_engine/browser/action_runner.py`
* **The Problem:** When an element selector fails, the test step immediately fails and terminates the journey.
* **Failure Impact:** Dynamic web apps with changing test environments frequently break static selectors.
* **Required Fix:** Add AI Self-Healing fallback: if a primary selector fails, search the current DOM for elements with matching text, ARIA roles, or semantic purpose, heal the step on-the-fly, and log a warning.

---

## 3. High-Value "Add-Ons" to Build a World-Class Platform

```
┌────────────────────────────────────────────────────────────────────────┐
│                   NEXT-GEN AI TESTING PLATFORM                         │
├────────────────────────────────┬───────────────────────────────────────┤
│ 1. DOM Distiller & Catalog    │ Extracts exact, stable element locators│
│ 2. Multi-Scenario Matrix       │ Generates 3-5 distinct test journeys  │
│ 3. Self-Healing Action Runner │ Recovers from broken selectors live    │
│ 4. ReAct Autonomous Agent      │ Observes DOM dynamically step-by-step │
│ 5. Visual Bug Bounding Boxes   │ VLM highlights visual defects in UI   │
│ 6. Natural Language Intent     │ "Test checkout flow with promo code"  │
│ 7. Adversarial QA Testing      │ Injects SQLi, XSS, and boundary values│
└────────────────────────────────┴───────────────────────────────────────┘
```

### 🌟 Add-On 1: DOM Distiller & Interactive Element Catalog
Instead of feeding raw HTML or button text to the LLM, inject a lightweight JavaScript crawler that distills the DOM into an indexed catalog:
```json
[
  {"index": 1, "role": "button", "text": "Sign In", "selector": "#nav-signin", "state": "visible"},
  {"index": 2, "role": "textbox", "label": "Email", "selector": "input[name='email']", "state": "empty"},
  {"index": 3, "role": "searchbox", "placeholder": "Search products...", "selector": "#site-search", "state": "visible"}
]
```
The LLM generates steps referencing verified index numbers (e.g., `{"action": "click", "element_index": 1}`), **completely eliminating selector hallucinations**.

---

### 🌟 Add-On 2: Multi-Scenario User Journey Matrix
Upgrade AI test generation from 1 single test case to an **Autonomous 4-Scenario Matrix**:
1. **Scenario 1 (Core Happy Path):** Main user conversion journey (e.g., signup, search, checkout).
2. **Scenario 2 (Validation & Guardrails):** Missing required fields, invalid emails, boundary values.
3. **Scenario 3 (Navigation & Wayfinding):** Primary header menus, footer links, back/forward button behavior.
4. **Scenario 4 (Interactive Widgets):** Modals, accordion tabs, filters, and mobile drawer toggles.

---

### 🌟 Add-On 3: AI Self-Healing Locators
When `ActionRunner` cannot find an element within 5 seconds:
1. Capture the immediate DOM surrounding the expected element.
2. Ask the local AI model:
   > *"The selector `#submit-btn` was not found. Find the closest matching button or clickable element in this DOM snippet."*
3. The AI suggests the new selector (e.g., `button.btn-primary[type='submit']`).
4. The test continues without crashing, and a **Self-Healed Locator** badge is displayed in the report.

---

### 🌟 Add-On 4: Multimodal Vision Bounding Boxes
Upgrade [UXVisionEvaluator](file:///home/hanuai/Documents/Office%20work%20projects/ai_testing_tool/ai_testing_tool_backend/ai_engine/prompts/ux_vision_evaluator.py) to ask Qwen-27B Vision for normalized bounding box coordinates:
```json
{
  "title": "Text overflows container",
  "box_2d": [340, 120, 420, 680],
  "severity": "MEDIUM",
  "suggested_fix": "Add CSS `overflow: hidden; text-overflow: ellipsis;`"
}
```
The frontend can render a red highlight box directly over the screenshot in the report viewer.

---

### 🌟 Add-On 5: Natural Language Intent Testing
Allow users to enter natural language test goals from the UI:
* *"Test user signup with invalid email format and verify error message"*
* *"Add an item to the shopping cart and check if the badge count increments"*
* *"Open mobile menu and verify all 5 category links work"*

The AI translates this high-level intent into Playwright steps and targeted assertions.

---

### 🌟 Add-On 6: Adversarial & Security Input Injection
Extend the Functional and Security pillars by instructing the AI to generate adversarial edge-case inputs:
* Cross-Site Scripting (XSS) probe payloads in search bars (`<script>alert(1)</script>`)
* SQL Injection evasion probes (`' OR '1'='1`)
* Unicode, emoji, and RTL (Right-to-Left) text handling in user input fields

---

## 4. Phased Implementation Roadmap

### 📍 Sprint 1 (Foundation & Reliability - P0)
- [ ] **Task 1.1:** Build DOM Distiller to provide real, verified selectors to the LLM.
- [ ] **Task 1.2:** Upgrade `test_generation.py` to produce a full 3–5 scenario test suite.
- [ ] **Task 1.3:** Support modern SPAs (`<div>` inputs, search widgets, modal dialogs).

### 📍 Sprint 2 (Intelligence & Robustness - P1)
- [ ] **Task 2.1:** Implement Self-Healing Selector recovery in `ActionRunner`.
- [ ] **Task 2.2:** Upgrade assertions to check actual UI state transitions and error messages.
- [ ] **Task 2.3:** Add multi-viewport visual audits (Mobile 375px + Desktop 1280px).

### 📍 Sprint 3 (Advanced Agent Autonomy - P2)
- [ ] **Task 3.1:** Implement full ReAct step-by-step observation agent.
- [ ] **Task 3.2:** Add visual bug bounding box overlays on screenshots.
- [ ] **Task 3.3:** Enable natural language goal-driven exploratory testing.

---

*This document serves as the official architecture backlog and reference specification for the AI engine enhancements.*
