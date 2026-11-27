# Accessibility Agent

Spec: [0011](../specs/0011-accessibility-agent/index.md). API: [api.md](../api.md#accessibility). Agent: [agents.md](../agents.md#12a-accessibilityagent).

## What it does

The Accessibility Agent checks whether people with disabilities, such as screen reader users and keyboard users, can use the project's application. It checks pages against WCAG 2.2 AA (the web accessibility guidelines) with axe-core, the standard rule engine. The AI explains each problem in plain words and suggests a fix. Serious problems become bug report drafts for Jira, and each run gets a release gate result that CI can read.

An automated scan catches only part of WCAG. A `PASS` gate is not a conformance claim, and every screen and report says so.

## Two ways to scan

* **During test runs.** With the project's scanning on (the default), every run checks each page it visits once, on both runners (the Python runner and the workspace engine). During the test the runner only records the URLs the page settles on. After the test, it opens each recorded page again in the same signed in browser and runs axe, so the test's own steps are never slowed. It stops at `accessibility.max_pages` pages (default 10) or `accessibility.page_budget_ms` of scanning (default 30 seconds). Skipped pages are listed with `error: budget`.
* **Standalone scans.** On the project dashboard, **Run accessibility scan** opens a dialog listing every page found for the project (the target URL, object repository pages, and pages named in test cases), all ticked. You can untick pages and add paths on the target URL. Wildcard object repository patterns are shown but cannot be scanned. Past scans are listed under the button, and **View** shows a scan's full report.

## Scans behind login

Tick **Include pages behind login** in the scan dialog (only for projects marked as a safe test environment, because it submits real credentials).

1. Every chosen page is opened with no session. A page that redirects to a page with a visible password field is marked as needing login and is not scanned.
2. The scan opens the project target URL, finds the login form (a visible password field and a username or email field next to it), fills the stored username and password, and submits.
3. The login counts as successful when the URL path changed, no password field is visible, and no `[role=alert]` message shows. The pages that needed login are then scanned signed in.
4. If the login fails, the report says `Login: failed`, and those pages stay listed as needing login.

The login form must be on the target URL itself. Multi step logins and single sign on are not supported; those projects get a public only scan.

## Reading a report

The **Accessibility** section of execution details, and each standalone scan report, shows:

* The gate badge and the gate mode used.
* Counts by impact (critical, serious, moderate, minor, needs review).
* **Violations** and **Needs human review**, each with the rule, WCAG criteria, pages, element, the AI explanation, the fix (or what a person must check), the element screenshot for critical and serious problems, and a link to its bug report.
* The scanned pages, with the reason any page was skipped.
* A "not scanned" state with its reason when the run was not scanned.

HTML and PDF reports carry the same section, and JUnit files add a `testsuite name="accessibility"`.

## Project settings

In project settings, under accessibility:

| Setting | Values | Default | Effect |
|---|---|---|---|
| `a11y_enabled` | on, off | on | Scan pages during test runs |
| `a11y_gate_mode` | `off`, `warn`, `block` | `warn` | `off` always passes; `warn` and `block` give `WARN` or `BLOCKED` |
| `a11y_block_severity` | `critical`, `serious` | `serious` | The lowest impact that counts for the gate and becomes a bug |

Each run uses the settings it started with. A later change never rewrites old gates.

## The release gate in CI

```
GET /api/v1/projects/{project_id}/accessibility/gate?execution_id=...
GET /api/v1/projects/{project_id}/accessibility/gate?batch_id=...
```

Send the `X-Webhook-Secret` header. A batch returns its worst run. A run that was not scanned is `NOT_SCANNED`, never a pass. The gate never changes a test's pass or fail result.

## Bugs

Each violation at or above the threshold becomes a bug draft in the **Accessibility Issue** category. One open bug covers one problem (rule plus element) per project, across pages, runs, and scans; a repeat adds an occurrence. A dismissed bug stays closed, and a resolved bug gets a new draft when the problem comes back. Needs review findings never become bugs. Bugs follow the project's auto submit setting and get the Jira labels `accessibility`, `a11y_<rule>`, and `wcag_<criterion>`.

## What is never stored or sent

Passwords, vault values, test data values, and input `value` attributes are removed before anything is saved or sent to the LLM. Snippets are capped at 500 characters. Credentials reach the browser only as `TARGET_USERNAME` and `TARGET_PASSWORD` environment variables and are never logged.

## Configuration

The `accessibility:` section of `backend/config/default_config.yaml`: `enabled`, `wcag_tags`, `max_pages`, `page_budget_ms`, `max_llm_findings`, `llm_timeout_seconds`, `max_element_screenshots`, `max_nodes_per_rule`, `max_pages_per_finding`, `webhook_payload_max_bytes`, and `scan_stale_after_seconds` (a standalone scan running longer is marked failed with `timeout`). `A11Y_ENABLED`, `A11Y_MAX_PAGES`, `A11Y_PAGE_BUDGET_MS`, and `A11Y_MAX_LLM_FINDINGS` override them. `enabled` (or `A11Y_ENABLED=false`) turns off all scanning on the server: test runs are not scanned, and starting a standalone scan returns `409`.

## Not covered yet

Keyboard and focus checks (Tab order, focus traps, missing focus outlines), popups and modals the test opened, SARIF export, and scheduled scans. See the spec's follow ups.
