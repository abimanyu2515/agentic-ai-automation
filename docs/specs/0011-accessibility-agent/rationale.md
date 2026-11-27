# 0011. Rationale: AI Accessibility Testing Agent

## Context

The platform turns requirements into Playwright tests and runs them against each project's target URL. It checks behavior only. Nothing checks whether the pages work for people who use screen readers, keyboards, or high contrast, so barriers such as unlabeled buttons, missing alt text, and low contrast ship unnoticed. Legal requirements (ADA, the European Accessibility Act, India's RPwD Act) make that a release risk, not only a quality issue.

The forces that shaped the design:
- **Two execution paths** already exist: the Python `PlaywrightRunner` (graph and batch runs) and the Next.js workspace engine, which finishes through a webhook. Coverage on only one path would leave runs unchecked without anyone noticing.
- **Existing building blocks**: vault credentials passed as env vars, per run artifact folders, the object repository harvest session (a model for a hook that runs on each page), `BugReportService` with signature dedupe and Jira submit (spec 0010), and the professional and legacy report engines.
- **Agent contract**: every agent owns its `WorkflowState` fields, and only BugReportingAgent writes `bug_report_ids`.
- **Limits of automation**: rule engines find only part of WCAG issues. Some results are "incomplete" and need a person to decide.
- **Data handling**: page HTML can hold user data and credentials, and LLM calls leave the system.

The feature was modelled on a commercial accessibility testing agent. That agent offers a WCAG 2.2 rule scan, keyboard and focus journeys, scans of pages behind login, evidence for each finding, flags for human review, release gates, and Jira and CI delivery. This spec takes the rule scan, the AI guidance, the bugs, and the gate as Phase 1.

## Options considered

### Option 1: Separate AccessibilityAgent node with a shared browser hook (chosen)

A new graph node and service read raw axe results that a browser hook (`a11y-scan-session.ts`) collects during the run. The same hook runs on the Python runner, on the workspace engine, and in a fixed template standalone scan.

**Pros**:
- One axe implementation for every path. It follows the agent contract and the harvest session pattern already in the code (basis: your AGENTS.md, the `WorkflowState` agent contract; spec 0004 harvest session).
- Pages behind login are scanned in the test's own signed in browser context, with no separate login.

**Cons**:
- The hook has to be wired into two execution paths plus the webhook payload.
- It makes every run longer.

### Option 2: Fold accessibility into ExecutionAnalysisAgent

The existing analysis node also runs the axe parse and the LLM guidance.

**Pros**:
- No new node, and one LLM step.

**Cons**:
- It mixes failure triage with an independent check. A run can pass functionally and still have violations.
- It breaks field ownership, and makes it hard to switch accessibility off or test it alone.

### Option 3: Standalone scanner only, in Python like the recon scan

Only an on-demand scan, run in process with Python Playwright, injecting `axe.min.js`.

**Pros**:
- Simple. It never touches the test run path.

**Cons**:
- It misses the pages and states the tests reach, and creates a second scan implementation.
- It gives no gate result per release.

### Option 4: A separate tool (Pa11y or Lighthouse CI) outside the platform

**Pros**:
- Ready made CLI and CI integration.

**Cons**:
- It launches its own browser, so it can't reuse the test's logged in page.
- Its results sit outside the platform's bugs, reports, and Jira flow.
- Lighthouse gives less detail for each element.

## Rationale

The decisive force is the two execution paths plus the need to reach pages behind login. Only a hook inside the test's own browser session has that session, and a single shared hook stops the Python and workspace paths from drifting apart (basis: spec 0004 object harvest session, the same pattern).

The hook records URLs during the test and runs axe in `afterEach`. It does not scan while the test acts, because axe running alongside test clicks can change focus and timing and make functional tests flaky. That would break the rule that accessibility never changes a run's result. The cost is that popups and states only partway through the test are not scanned. Phase 2's keyboard walk is where those states belong (basis: an isolated hook keeps test results deterministic). A separate node, rather than folding into analysis, keeps the agent contract intact. AccessibilityAgent owns `accessibility_results`, BugReportingAgent keeps sole ownership of `bug_report_ids`, and a functionally passing run can still report barriers (basis: your AGENTS.md agent rules).

axe-core is the boring, standard engine. It exposes both violations and incomplete results, with selectors, which map directly onto the findings and human review model (basis: WCAG 2.2; axe-core rule tags). Pa11y and Lighthouse wrap the same or a smaller rule set and can't share the test's page.

Reusing `BugReportService` gives dedupe, redaction, review before Jira, auto-submit, and Jira duplicate search for free (basis: spec 0010). Its "one bug per test case signature" rule doesn't fit, because a shared header bug repeats across every test case. So accessibility bugs dedupe per project by finding signature, and `test_case_id` and `execution_id` become nullable for standalone scans. A JSON findings column was picked over a findings table because findings are always read with their report, and the JSON store already saves whole objects. Cross report questions are answered through bug signatures.

The gate is separate from the functional status so that turning on accessibility never reddens existing pipelines. `NOT_SCANNED` exists so a broken scanner can't pass a release silently (basis: fail closed visibility for release gates). The defaults (`warn`, `serious`) give findings from day one without blocking anyone.

### Workspace run gate settings (added 2026-11-26)

**Context.** AC-8 computes the gate from the run's `project_config_snapshot`, saved at run start. Graph and batch runs start in the backend, so they have one. Workspace runs don't: the browser calls the Next.js engine directly, the backend first hears of the run at the `started` webhook (which only adds in memory events, with no saved row and no `RUNNING` status), and `finalize_execution` built the `ExecutionResult` with no snapshot. So the gate had no named settings source for that path, and bug drafts had no `target_url` or `framework` either. The engine already reads `scan-settings` once at run start to decide whether to scan.

**Options considered.**
- *Capture at the `started` webhook.* Truest run start time. Con: needs a placeholder execution row and a new `RUNNING` status that every history list and report would have to handle. The webhook is also best effort (the engine swallows its failures), so the snapshot would still be missing sometimes.
- *Capture at finalize only.* The simplest, with no engine change. Con: the gate reads settings as they are when the run ends, so the drift window equals the run length. The scan decision (made by the engine at start) and the gate (made at the end) can also disagree. A project switched off mid run would show a scanned run as `NOT_SCANNED` with `disabled`.
- *Engine echo only.* The engine sends back the values it read. Con: it leaves bug drafts without a snapshot, and gives no answer when the read failed.
- *Echo plus finalize capture (chosen).* The engine echoes `a11y_settings`, and finalize builds a full snapshot and overlays the echo field by field. Con: the engine changes, and non accessibility fields are read at finalize, not run start.

**Rationale.** The one read that decides whether a workspace run is scanned also decides how it is gated, so the two can never disagree, and both are true run start values. That is the property AC-8 exists for (basis: fail closed visibility for release gates). Building the rest of the snapshot at finalize gives workspace runs the same dict graph runs have, so `AccessibilityService.process` and bug drafting need no workspace branch. It also reuses the "keep the first snapshot" rule both repositories already have, which makes retries safe for free (basis: spec 0001 immutable run snapshot). The echo is trusted without signing, because the caller holding `X-Webhook-Secret` already supplies the run's results and could fake those anyway. A signature would add a key and code without closing a real gap. Each field is checked against its allowed values and falls back alone, so one bad value never throws away good ones and never fails the webhook (AC-2). When the read failed, finalize uses the project's current values, and a scanning project with no raw data gets `scan_error`. A missing check stays visible and never counts as a pass. When the project isn't ready at finalize, the readiness check is skipped rather than saving no snapshot, because a null snapshot would quietly put a `block` project back on the `warn` default.

The echoed `enabled` is passed to `process` instead of being written into the snapshot. It is the engine's combined answer (project AND global switch), and storing it as `a11y_enabled` would record a project setting the project never had. Checking the global switch again at finalize could also mark a run that really was scanned as `disabled`. The snapshot is resolved before triage, because triage and its bug draft read it. Accessibility processing reads the persisted copy after the save, so a retry can never gate on a different snapshot than the stored one.

Rejected details, with their runners up:
- **Field by field fallback for a bad echo.** Runner up: drop the whole echo when any field is bad (simpler rule, but loses a good `enabled` value).
- **Fail closed `scan_error` when no echo arrives.** Runner up: a new `gate_reason=settings_unavailable` (more precise for CI, but one more enum value for a rare case).
- **Per run LLM cap of 20, timeout 60 seconds.** Bounds cost and run time. Runner up: enrich every finding in the background (slower feedback, bugs drafted without AI text).
- **Generic login form fill for standalone scans.** No coupling to test cases. Runner up: replay an approved login test case (handles odd logins, couples the scan to test case health).
- **Persisted scan status.** Survives restarts. Runner up: the in memory registry used by the object repository scan.

## References

**Project sources**:
- `AGENTS.md` (root, backend, services): the `WorkflowState` agent contract, thin routes, repository abstraction, vault env vars, dependency injection in `workflow.py`, documentation rule
- Spec 0001 project specific testing: the run's `project_config_snapshot`, written once and never replaced
- Spec 0004 object repository: the harvest session hook and `ObjectRepositoryScanService.scan_urls()`
- Spec 0009 route permission checks: the `Executions.view` and `Executions.edit` pattern
- Spec 0010 Bug Reporting Agent: drafts, signature dedupe, redaction, Jira submit, auto-submit

**Practices & standards**:
- WCAG 2.2 Level AA (W3C Web Content Accessibility Guidelines)
- axe-core rule tags and result types (violations, incomplete)
- Fail closed visibility for release gates (a missing check is never reported as a pass)
- Redact before sending data to external processors
