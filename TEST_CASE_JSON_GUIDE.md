  # Test Case JSON Authoring & Schema Guide

This guide defines how to construct, format, and generate structured test cases for the **AI Website Testing Platform**.  
It can be used by QA engineers, frontend developers, and AI prompt engineers authoring automated test suites for the `POST /api/v1/test-runs/execute-custom` endpoint.

---

## 1. Quick Start: The 2 Ways to Use This JSON

### Method A: Uploaded `.json` File (Frontend UI)
If you are uploading a file through the frontend UI, your file can contain just the `cases` list:

```json
{
  "cases": [
    {
      "id": "TC-001",
      "title": "Verify Contact Us Form Submission",
      "pillar": "functional",
      "risk": "read_only",
      "tags": ["smoke", "contact"],
      "steps": [
        { "action": "open_page", "value": "https://example.com/contact" },
        { "action": "type_text", "target": "#name", "value": "Alice Smith" },
        { "action": "type_text", "target": "#email", "value": "alice@example.com" },
        { "action": "click", "target": "button[type='submit']" }
      ],
      "assertions": [
        { "type": "element_visible", "target": ".alert-success" },
        { "type": "text_contains", "target": ".alert-success", "value": "Thank you" }
      ]
    }
  ]
}
```
*(Or even directly as a JSON array `[ { "id": "TC-001", ... } ]`)*.

---

### Method B: Full Direct API Request
When calling `POST /api/v1/test-runs/execute-custom` directly via cURL or Postman:

```json
{
  "url": "https://demoqa.com",
  "name": "DemoQA Regression Suite",
  "credentials": {
    "enabled": false
  },
  "options": {
    "check_functional": true,
    "check_security": false,
    "check_performance": false,
    "check_accessibility": false,
    "browser_type": "chromium",
    "timeout_seconds": 180,
    "ai_explain_failures": true
  },
  "cases": [
    { ... }
  ]
}
```

---

## 2. Test Case Schema Reference (`cases[...]`)

Each test case inside the `"cases"` array must follow this schema:

| Field | Type | Required | Default | Description | Example |
|---|:---:|:---:|:---:|---|---|
| `id` | `string` | **Yes** | — | Unique identifier for this test case | `"TC-AUTH-001"` |
| `title` | `string` | **Yes** | — | Short human-readable title | `"User Login with Valid Password"` |
| `pillar` | `string` | No | `"functional"` | Pillar classification (`functional`, `security`, `performance`, `accessibility`) | `"functional"` |
| `risk` | `string` | No | `"read_only"` | Safety level: `"read_only"` or `"state_changing"` | `"read_only"` |
| `tags` | `array[string]` | No | `[]` | Categorical tags for suite filtering and reporting | `["auth", "smoke", "p0"]` |
| `preconditions`| `array[string]` | No | `[]` | Prerequisites required before executing steps | `["clean_session"]` |
| `steps` | `array[object]` | **Yes** | — | Ordered list of Playwright browser operations | *(See Section 3)* |
| `assertions` | `array[object]` | **Yes** | — | List of assertions that must pass for green status | *(See Section 4)* |
| `test_data` | `object` | No | `{}` | Key-value dictionary of test parameters/variables | `{"username": "demo"}` |
| `credentials_ref` | `string` | No | `null` | Key name of stored credential set | `"standard_user"` |
| `cleanup` | `array[object]` | No | `[]` | Rollback steps executed even if test fails | `[{"action": "click", ...}]` |
| `evidence` | `array[string]` | No | `["screenshot", "assertion_log"]` | Artifacts to collect on completion/failure | `["screenshot", "trace"]` |

---

## 2.1 Key-by-Key Decision Guide: What to Choose & When

### 1. `id` (Unique Identifier)
* **What to choose:** A short, uppercase, hyphenated string following the convention `TC-<MODULE>-<NUMBER>`.
* **When to choose what:**
  * **By Module:** Use clear domain abbreviations:
    * Authentication: `"TC-AUTH-001"`, `"TC-AUTH-002"`
    * Search & Discovery: `"TC-SEARCH-001"`
    * Checkout & Cart: `"TC-CHECKOUT-001"`
    * Forms & Leads: `"TC-FORM-001"`
    * Navigation & Menus: `"TC-NAV-001"`
  * **Critical Rule:** Keep `id` **stable and immutable** across test runs. The backend generates SHA-1 tracking fingerprints keyed on `id` to compute run-over-run regressions, flaky test detection, and defect history. Never use random IDs, temporary prefixes, or dynamic timestamps (e.g. `test_12345`).

### 2. `title` (Human-Readable Summary)
* **What to choose:** A concise sentence describing the user journey and expected outcome. Follow the formula:  
  **`[Action / Condition] + [Expected Outcome]`**
* **When to choose what:**
  * **Good Examples:**
    * `"Submit contact form with valid email and verify confirmation alert"`
    * `"Search for out-of-stock product and verify 'No results found' banner"`
    * `"Click checkout button with empty shopping bag and verify redirect blocked"`
  * **Bad Examples:** `"Test 1"`, `"Check login"`, `"Button click"`.
  * **Why it matters:** When an assertion fails, the AI diagnostic engine uses the `title` to understand the test's intended business logic before analyzing page DOM and failure screenshots. A clear title produces accurate root-cause explanations.

### 3. `pillar` (Quality Pillar Classification)
* **What to choose:** One of the 4 platform pillars:
  * `"functional"` (Default)
  * `"security"`
  * `"performance"`
  * `"accessibility"`
* **When to choose what:**
  * **Choose `"functional"`:** For 90%+ of automated flows — button clicks, form fills, shopping cart journeys, tabs, modals, pagination, and data display.
  * **Choose `"security"`:** When testing authentication boundaries, URL access controls, session timeout redirects, CSRF tokens, or checking that sensitive fields (credit cards, passwords) are masked.
  * **Choose `"performance"`:** When checking page load indicators, lazy-loading thresholds, or asserting that network operations complete within acceptable time limits.
  * **Choose `"accessibility"`:** When verifying keyboard tab navigation, ARIA attributes, image `alt` texts, or color contrast elements.

### 4. `risk` (Safety Guardrail Level)
* **What to choose:** Either `"read_only"` or `"state_changing"`.
* **When to choose what:**
  * **Choose `"read_only"` (RECOMMENDED for 95% of tests):**
    * Use when the test only queries or views data without mutating backend storage.
    * Examples: Page navigation, searching products, viewing profiles, downloading PDFs, checking dropdown options, or submitting mock validation forms that don't commit persistent data.
  * **Choose `"state_changing"`:**
    * Use **ONLY** when the test performs permanent mutations, writes to a production database, triggers external billing, sends real emails/SMS, or deletes resources.
    * Examples: `"Delete Account"`, `"Charge Credit Card"`, `"Change Admin Password"`, `"Submit Non-Refundable Order"`.
  * **Safety Policy:** If the scan profile has `allow_state_changing: false` (the default mode), any test case marked `"state_changing"` is **automatically blocked and skipped** by the `ScopeGuard` to prevent unintended data loss or destructive mutations.

### 5. `tags` (Filtering & Categorization)
* **What to choose:** An array of lowercase strings defining the scope, priority, and functional area.
* **When to choose what:**
  * **By Priority:**
    * `"p0"`: Blocker / Mission Critical (Revenue-critical flows like checkout, login).
    * `"p1"`: High Priority (Search, filtering, contact forms).
    * `"p2"`: Medium / Low Priority (Footer links, dark mode toggle).
  * **By Test Phase:**
    * `"smoke"`: Fast sanity tests run on every code commit or staging deploy (< 1 minute).
    * `"regression"`: Comprehensive full suite run nightly or pre-release.
  * **By Flow:** `"auth"`, `"checkout"`, `"cart"`, `"forms"`, `"navigation"`, `"negative"`, `"demoqa"`.
  * *Tip:* Tagging enables you to run targeted subsets (e.g. running only `tags: ["smoke"]`).

### 6. `preconditions` (Initial State Assumptions)
* **What to choose:** An array of condition strings.
* **When to choose what:**
  * `["clean_session"]`: Default. Starts with an incognito browser context, empty cookies, and blank local storage.
  * `["authenticated_user"]`: Expects the test engine to pre-authenticate using the credentials payload before executing the first step.
  * `["empty_cart"]`: Signifies cart must have zero items before starting.

### 7. Optional Advanced Keys (`test_data`, `credentials_ref`, `cleanup`, `evidence`)
* **`test_data`**: Use when creating parameterized or data-driven test cases (e.g. `{"searchTerm": "laptop", "minPrice": 500}`). Allows separating test logic from input data.
* **`credentials_ref`**: Use when your test suite defines multiple user roles (e.g. `"admin_user"`, `"guest_user"`). Points to the matching credential set in the execution payload.
* **`cleanup`**: Use with `"state_changing"` tests to specify rollback actions (e.g. navigating to account settings and clicking "Delete Created Item").
* **`evidence`**: Defaults to `["screenshot", "assertion_log"]`. Add `"trace"` to record Playwright `.zip` execution traces for deep inspection.

---

## 3. Supported Browser Actions (`steps`)

Every step in the `steps` array represents a real Playwright browser operation. Steps execute sequentially.

### Action Schema Reference

| Action (`action`) | Target (`target`) | Value (`value`) | Description | Example |
|---|---|---|---|---|
| `open_page` | Optional | **Required** (URL) | Navigates the browser to a URL | `{"action": "open_page", "value": "https://example.com/login"}` |
| `click` | **Required** (Selector) | Optional | Clicks on the specified element | `{"action": "click", "target": "button#submit"}` |
| `type_text` | **Required** (Selector) | **Required** (Text) | Clears input and types text string | `{"action": "type_text", "target": "#email", "value": "user@test.com"}` |
| `select_option` | **Required** (Selector) | **Required** (Value) | Selects a native `<select>` dropdown option | `{"action": "select_option", "target": "#country", "value": "US"}` |
| `scroll` | **Required** (Selector) | Optional | Scrolls the element into the active viewport | `{"action": "scroll", "target": "#footer-submit"}` |
| `hover` | **Required** (Selector) | Optional | Hovers the mouse cursor over element | `{"action": "hover", "target": ".nav-dropdown"}` |
| `wait` | Optional | Optional | Explicit pause for animations/SPA lag (`timeout_ms`) | `{"action": "wait", "timeout_ms": 1000}` |
| `download` | **Required** (Selector) | Optional | Clicks element and awaits file download | `{"action": "download", "target": "#download-pdf"}` |

### Action Decision Guide: What Action to Choose and When

| Scenario | Recommended Action | Why & Best Practice |
|---|---|---|
| **First step of any test** | `open_page` | Every test case must start by opening the target URL. |
| **Typing into `<input>` or `<textarea>`** | `type_text` | Automatically clears existing value before typing. Target must point to the input element. |
| **Submitting forms, buttons, links, checkboxes** | `click` | Standard click. For radio buttons and checkboxes, click toggles state. |
| **Native `<select>` dropdown** | `select_option` | Use `value` with the `<option value="...">` or visible label text. |
| **Custom Div / React dropdown (e.g. Material-UI, AntD)** | `click` (twice) | Custom dropdowns are NOT `<select>`. First `click` the dropdown toggle, then `click` the option item. |
| **Element is offscreen / below fold** | `scroll` + `click` | If a button is offscreen or sticky footer obscures it, add a `scroll` step before the `click`. |
| **Hover menu / mega-nav / tooltip** | `hover` | Triggers CSS `:hover` or JS mouseenter to reveal hidden sub-menus. |
| **React/Vue/Next.js SPA hydration lag** | `wait` (`500`-`1500`ms) | Add a small `wait` step immediately after `open_page` if JavaScript event handlers take a second to bind. |

### Best Practices for `target` Selectors
* **ID Selectors (Best):** `#submit`, `#userName`, `#login-btn`
* **Name Attributes:** `input[name='email']`, `input[name='password']`
* **Test IDs:** `[data-testid='add-to-cart']`, `[data-cy='checkout']`
* **CSS Hierarchy:** `form.login-form button[type='submit']`
* **Playwright Roles:** `role=button[name='Sign In']`, `role=link[name='Pricing']`

---

## 4. Supported Assertions (`assertions`)

Assertions verify that the page responded correctly. If any assertion fails, the test is marked failed, a full-page screenshot is automatically captured, and the failure is sent to the AI for root-cause diagnosis.

### Assertion Schema Reference

| Assertion Type (`type`) | Target (`target`) | Expected Value (`value`) | Description | Example |
|---|---|---|---|---|
| `element_visible` | **Required** (Selector) | None | Asserts element is present in DOM and visible | `{"type": "element_visible", "target": "#dashboard"}` |
| `element_hidden` | **Required** (Selector) | None | Asserts element is absent, detached, or invisible | `{"type": "element_hidden", "target": ".loading-spinner"}` |
| `text_contains` | **Required** (Selector) | **Required** (String) | Asserts element text contains substring | `{"type": "text_contains", "target": "h1", "value": "Welcome"}` |
| `text_equals` | **Required** (Selector) | **Required** (String) | Asserts element text matches exactly | `{"type": "text_equals", "target": ".badge", "value": "Active"}` |
| `url_contains` | None | **Required** (String) | Asserts browser URL contains substring | `{"type": "url_contains", "value": "/dashboard"}` |
| `page_title_contains` | None | **Required** (String) | Asserts `<title>` contains substring | `{"type": "page_title_contains", "value": "Home"}` |
| `status_code_equals` | None | **Required** (Integer) | Asserts primary page HTTP response code | `{"type": "status_code_equals", "value": 200}` |
| `option_list_not_empty`| **Required** (Selector) | None | Asserts `<select>` contains at least 1 option | `{"type": "option_list_not_empty", "target": "#city"}` |

### Assertion Decision Guide: What Assertion to Choose and When

| Verification Goal | Recommended Assertion | Why / When to Choose |
|---|---|---|
| **Verifying a UI component appeared** | `element_visible` | Use when checking modals, banners, success badges, cards, or tables. Does not depend on fragile copy. |
| **Verifying loaders or modals dismissed** | `element_hidden` | Use after submitting a form to ensure `.loading-spinner` or `#modal-backdrop` has disappeared. |
| **Verifying error or confirmation text** | `text_contains` | **(Recommended over `text_equals`)** Checks for substring. Resilient against extra spaces, timestamps, or surrounding punctuation. |
| **Verifying exact status codes or badges** | `text_equals` | Use only when the text is short, rigid, and uppercase (e.g. `"PAID"`, `"SUCCESS"`, `"ACTIVE"`). |
| **Verifying route transitions & redirects** | `url_contains` | Perfect for verifying login redirect (e.g. `{"type": "url_contains", "value": "/dashboard"}`) or search queries (`"search?q="`). |
| **Verifying correct page loaded** | `page_title_contains` | Great for quick SEO / sanity checks on landing pages without coupling to DOM selectors. |
| **Verifying server health / response** | `status_code_equals` | Verifies HTTP status code (e.g. `200` for OK, `404` for not found checks). |
| **Verifying cascading dropdowns populated** | `option_list_not_empty` | When choosing a State/Province dynamically populates City options from an API. |

## 5. Ready-to-Use Real-World Examples

### Example 1: Form Fill & Submission (DemoQA Text Box)
```json
{
  "id": "TC-DEMOQA-01",
  "title": "Submit DemoQA text box form and verify output echo",
  "pillar": "functional",
  "risk": "read_only",
  "tags": ["forms", "demoqa"],
  "steps": [
    {
      "action": "open_page",
      "value": "https://demoqa.com/text-box",
      "description": "Open Text Box page"
    },
    {
      "action": "wait",
      "timeout_ms": 1000,
      "description": "Wait for DOM hydration"
    },
    {
      "action": "type_text",
      "target": "#userName",
      "value": "Jane Doe",
      "description": "Fill full name"
    },
    {
      "action": "type_text",
      "target": "#userEmail",
      "value": "jane.doe@example.com",
      "description": "Fill valid email"
    },
    {
      "action": "scroll",
      "target": "#submit",
      "description": "Scroll down to submit button"
    },
    {
      "action": "click",
      "target": "#submit",
      "description": "Click submit button"
    }
  ],
  "assertions": [
    {
      "type": "element_visible",
      "target": "#output",
      "description": "Output box must become visible"
    },
    {
      "type": "text_contains",
      "target": "#output #name",
      "value": "Jane Doe",
      "description": "Output must echo entered full name"
    }
  ]
}
```

---

### Example 2: E-Commerce Product Search & Verification
```json
{
  "id": "TC-SEARCH-02",
  "title": "Search for product and verify result list appears",
  "pillar": "functional",
  "risk": "read_only",
  "tags": ["search", "ecommerce"],
  "steps": [
    {
      "action": "open_page",
      "value": "https://example-shop.com"
    },
    {
      "action": "type_text",
      "target": "input[type='search']",
      "value": "wireless headphones"
    },
    {
      "action": "click",
      "target": "button[type='submit']"
    },
    {
      "action": "wait",
      "timeout_ms": 1500
    }
  ],
  "assertions": [
    {
      "type": "url_contains",
      "value": "search?q=wireless"
    },
    {
      "type": "element_visible",
      "target": ".product-grid"
    },
    {
      "type": "text_contains",
      "target": ".search-summary",
      "value": "results"
    }
  ]
}
```

---

### Example 3: Invalid Email Error Message Verification
```json
{
  "id": "TC-VALIDATION-03",
  "title": "Verify form blocks invalid email and displays error class",
  "pillar": "functional",
  "risk": "read_only",
  "tags": ["validation", "negative"],
  "steps": [
    {
      "action": "open_page",
      "value": "https://demoqa.com/text-box"
    },
    {
      "action": "type_text",
      "target": "#userName",
      "value": "Test User"
    },
    {
      "action": "type_text",
      "target": "#userEmail",
      "value": "invalid-email-address@"
    },
    {
      "action": "scroll",
      "target": "#submit"
    },
    {
      "action": "click",
      "target": "#submit"
    }
  ],
  "assertions": [
    {
      "type": "element_visible",
      "target": "input#userEmail.field-error"
    }
  ]
}
```

---

## 6. System Prompt for AI Models (To Auto-Generate Tests)

If you want an LLM (such as ChatGPT, Claude, or Qwen) to automatically author these JSON files from a website description or HTML snippet, copy and paste this system prompt:

````markdown
You are an expert QA Automation Engineer specialized in Playwright testing.
Your task is to generate valid, automated test cases matching the platform schema.

OUTPUT REQUIREMENTS:
Output ONLY valid JSON matching this schema:
{
  "cases": [
    {
      "id": "TC-001",
      "title": "Clear description of what this verifies",
      "pillar": "functional",
      "risk": "read_only",
      "tags": ["smoke", "feature_name"],
      "preconditions": [],
      "steps": [
        { "action": "open_page", "value": "https://..." },
        { "action": "type_text", "target": "#selector", "value": "..." },
        { "action": "click", "target": "button[type='submit']" }
      ],
      "assertions": [
        { "type": "element_visible", "target": ".expected-container" },
        { "type": "text_contains", "target": ".result-text", "value": "Expected Text" }
      ]
    }
  ]
}

SUPPORTED ACTIONS:
- open_page (requires 'value' with URL)
- click (requires 'target' selector)
- type_text (requires 'target' selector and 'value' text)
- select_option (requires 'target' selector and 'value' option)
- scroll (requires 'target' selector)
- hover (requires 'target' selector)
- wait (optional 'timeout_ms' e.g. 1000)

SUPPORTED ASSERTIONS:
- element_visible (requires 'target')
- element_hidden (requires 'target')
- text_contains (requires 'target' and 'value')
- text_equals (requires 'target' and 'value')
- url_contains (requires 'value')
- page_title_contains (requires 'value')

CRITICAL RULES:
1. Always use precise, robust CSS selectors (prefer IDs, name attributes, or unique button classes).
2. Never hallucinate selectors that are not in the page context.
3. Output raw JSON only. Do not wrap in markdown or commentary.
````

---

## 7. Common Pitfalls & How to Avoid Them

| Pitfall | Why It Fails | How to Fix |
|---|---|---|
| **Missing `#` in ID selector** | Writing `"target": "submit"` instead of `"target": "#submit"` targets a `<submit>` HTML tag which doesn't exist. | Always prefix IDs with `#`: `"#submit"`. |
| **Element not in viewport** | Clicking a button that is offscreen before scrolling. | Add a `{"action": "scroll", "target": "#button"}` step before clicking. |
| **SPA Hydration Lag** | Typing into an input field before React/Next.js has attached event listeners. | Add a short wait `{"action": "wait", "timeout_ms": 1000}` after `open_page`. |
| **Typo in Action or Assertion** | Writing `"action": "type"` or `"type": "visible"`. | Use the exact names: `"action": "type_text"` and `"type": "element_visible"`. |
