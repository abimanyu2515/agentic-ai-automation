# Verify: AI Accessibility Testing Agent · spec 0011 · 2026-11-25
_Steps derived from spec 0011 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

Setup:
- Run `alembic upgrade head`, then `pnpm --dir backend/playwrightt install`, then start the backend.
- Use a project with a target URL, vault credentials, and at least one scripted test case. A page with known problems (an image without alt text, an unlabeled input, low contrast text) makes the checks clear.
- To keep Jira untouched, leave the `JIRA_*` env vars unset (mock mode).

## UI / manual
- [x] Run a test case → the run folder has `a11y.json`, and the execution details show an Accessibility section with the gate badge, counts, and findings → AC-1, AC-12
- [x] Open a finding → it shows the rule, WCAG criteria, pages, element, an AI explanation, and a fix. No password or input value appears → AC-3, AC-4, AC-5
- [ ] A serious violation → a bug report draft with category Accessibility Issue appears in Bug Reports. Run again → the same draft shows 2 occurrences → AC-6
- [ ] Submit the draft → the Jira issue has the labels `accessibility`, `a11y_<rule>`, and `wcag_<criterion>`, plus the element screenshot → AC-7
- [ ] Project settings: set the gate to block → the next run with a serious violation shows BLOCKED, and the functional result is unchanged → AC-8, AC-13
- [ ] Project settings: turn accessibility off → the next run shows Not scanned (disabled) → AC-8, AC-13
- [ ] Project page → Run accessibility scan → the checklist lists the target URL, object repository pages, and test case URLs, all ticked, with wildcards marked skipped. Add a custom path and start → the report moves from queued to running to succeeded → AC-10, AC-12
- [ ] Start a scan with include login on a safe test environment → `login_status` is succeeded and pages behind login are scanned. On a project that is not a safe environment → refused → AC-11
- [ ] Open the HTML and PDF report of a scanned run → the Accessibility section and the "does not certify WCAG conformance" line are present → AC-14
- [ ] Tab through the Accessibility section and the scan dialog → every control is reachable and named → AC-12

## Commands
- [ ] `pytest -q backend/tests/test_accessibility_service.py backend/tests/test_accessibility_agent.py backend/tests/test_accessibility_routes.py` → all pass → AC-2 to AC-11, AC-13, AC-15
- [ ] `pytest -q backend/tests/test_accessibility_browser.py` → axe finds the fixture's known violations (skipped if Chromium is missing) → AC-1, AC-3
- [ ] `pytest -q backend/tests/test_bug_reports.py backend/tests/test_graph.py` → all pass, including the accessibility draft and node order → AC-6, AC-7
- [ ] `GET /api/v1/projects/<id>/accessibility/gate?batch_id=<batch with one blocked run>` with the workspace secret → `BLOCKED` with a per run list → AC-9
- [x] `GET .../accessibility/gate` with both or neither id → 422 → AC-9
- [ ] `POST .../accessibility/scans` twice quickly → the second returns 409. A path on another host → 422 → AC-10
- [ ] As a user without `Executions.edit` → `POST .../accessibility/scans` is refused. A report id from another project → 404 → AC-15
- [ ] With a test stub that makes the scan session throw → the test result is unchanged, and the report is `failed` and `NOT_SCANNED` with `scan_error`. A page with a strict CSP → only that page shows `csp_blocked` → AC-2
- [ ] Repost the same workspace `completed` webhook → still one report for that execution → AC-1
- [ ] Change the gate mode after a run → that run's gate is unchanged, and the response shows the old `gate_mode` → AC-8
- [ ] `GET /api/v1/accessibility/scan-settings?project_id=<id>` with the workspace secret → includes `gate_mode` and `block_severity` matching the project → AC-8, AC-13
- [ ] Post a workspace `completed` webhook with `a11y_settings: {enabled: true, gate_mode: "block", block_severity: "serious"}` and a serious violation, for a project now set to `warn` → the report is BLOCKED with `gate_mode: "block"`, and the saved execution has a full `project_config_snapshot` (`target_url`, `framework`, a11y fields) → AC-8, AC-13
- [ ] Same webhook with `gate_mode: "strict"` → the webhook returns 200, the gate uses the project's mode, and the log shows `a11y_settings field ignored: gate_mode` → AC-2, AC-8
- [ ] A workspace `completed` webhook with no `a11y_settings` and no `a11y`, for a project with scanning on → `NOT_SCANNED` with `scan_error` → AC-2, AC-8
- [ ] Repost the first webhook after switching the project to `off` → the snapshot and the BLOCKED gate are unchanged → AC-8
- [ ] A failed workspace run (a script that throws) → the bug draft made by triage has `target_url` and `framework` from the new snapshot → AC-8
- [ ] A workspace `completed` webhook with `a11y_settings.enabled: true`, with the global `A11Y_ENABLED=false` set only after the run started → the report is processed (not `disabled`), and the snapshot's `a11y_enabled` equals the project's own value → AC-1, AC-8
- [ ] A workspace `completed` webhook for a project with no target URL → 200, and the snapshot has `target_url: ""` and the a11y values → AC-2, AC-8
- [ ] A workspace `completed` webhook with `a11y_settings_error: "timeout"` → `NOT_SCANNED` with `scan_error`, and the log names the engine read failure → AC-2, AC-8
- [ ] Run a batch of two scripted test cases → each case's run folder has `a11y.json`, each run has a report whose `batch_id` is the batch id, and `GET .../accessibility/gate?batch_id=<id>` lists both runs → AC-1, AC-9
- [ ] Run a test case through the workspace engine (Next.js execution page) → the `completed` webhook carries `a11y_settings` and `a11y`, the scan happens after the test steps (timeline order), and the execution details show the report → AC-1, AC-8
- [ ] A workspace run on a page with many violations, with `accessibility.webhook_payload_max_bytes` set low (for example 10000) → the webhook still returns 200, and the report has `truncated: true` → AC-1, AC-2
- [ ] Delete the project while a workspace run is still going, then let it complete → the webhook returns 200, the log says accessibility processing was skipped, and no report is saved → AC-2
- [ ] Dismiss an accessibility bug, then run again → no new draft. Resolve one, then run again → a new draft → AC-6
- [ ] `GET .../accessibility/reports/<id>/screenshots/..%2Fsecret` → 404 → AC-12
- [ ] JUnit for a scanned run has `testsuite name="accessibility"` with failing test cases for its violations → AC-14
- [ ] `cd backend && npx playwright test e2e/accessibility.spec.ts --project=chromium` → the Accessibility section renders from a stubbed API → AC-12

## Slice 1 (thin thread) · added 2026-11-25
_Steps for what Slice 1 built: the graph run path (single test case runs), storage, the execution and gate routes, and the execution details section. Batch runs, workspace runs, project settings, the LLM text, and bugs come in later slices._

### UI / manual
- [x] Run one test case from the UI against a page with known problems → `backend/playwrightt/artifacts/<test_case_id>/a11y.json` exists, and execution details show an Accessibility section with a Warn badge, counts by impact, Violations and Needs human review groups, and the "does not certify WCAG conformance" line → AC-1, AC-8, AC-12
- [x] In that run, visit the same page twice with a different query string or hash → `a11y.json` lists that path once → AC-1
- [x] Open a finding → rule, WCAG criteria (such as `1.1.1`), pages, element, an explanation (from axe for now), and a fix. An input that held a vault password or a typed value shows `[redacted]` or no `value` attribute → AC-3, AC-5
- [x] Open an old run from before this change → the section says it has no accessibility report (Not scanned) → AC-9, AC-12
- [x] Tab through the section → every finding opens with Enter or Space, links say where they go, and focus is visible → AC-12
- [ ] Run a test whose script takes longer than the axe budget leaves → the test still passes, and skipped pages show `budget` → AC-1, AC-2

### Commands
- [x] `pytest -q backend/tests/test_accessibility.py` → all pass → AC-1 to AC-3, AC-5, AC-8, AC-9 (route part), AC-15
- [x] `pytest -q` → the full suite passes (apply `alembic upgrade head` first) → no regressions
- [x] `alembic upgrade head`, then inspect `accessibility_reports` → the table, the unique `execution_id`, the `batch_id` and `(project_id, created_at)` indexes, and the cascading project foreign key exist → AC-15
- [x] `GET /api/v1/projects/<id>/accessibility/gate?execution_id=<run>` with `X-Webhook-Secret` → `{gate_status, gate_reason, gate_mode, runs}`. With a wrong secret → 401. With neither or both ids → 422. A run from another project → 404 → AC-9, AC-15
- [ ] `GET /api/v1/projects/<id>/executions/<run>/accessibility` as a user without project access → 403. As a Stakeholder on the project → 200 → AC-15
- [x] Set `A11Y_ENABLED=false` and run a test case → no `a11y.json`, and the report says Not scanned (`disabled`) → AC-8
- [x] Make the scan fail (for example, rename `node_modules/@axe-core` for one run) → the test result is unchanged, and the report is `failed`, `NOT_SCANNED`, `scan_error` → AC-2
- [x] Delete a project → its accessibility reports are gone from the store (JSON file or table) → AC-15

### Value sourcing (Slice 1 rows)
- [x] Which pages: navigate with `pushState` only (a single page app route change) → that path is scanned too → AC-1
- [ ] Raw results: the graph run reads `ARTIFACTS_ROOT/<test_case_id>/a11y.json`; delete it before the agent runs (for example in a debugger) → `scan_error` → AC-2
- [x] Report project, test case, batch: the saved report's `project_id`, `test_case_id`, and `batch_id` match the run → AC-15
- [ ] WCAG tags and caps: set `A11Y_MAX_PAGES=1` and visit two pages → the second shows `budget` → AC-1
- [x] Finding `wcag_criteria`: a `color-contrast` finding shows `1.4.3` (from the axe tag `wcag143`) → AC-3
- [ ] Gate mode and threshold: a run started with `a11y_gate_mode=block` in its snapshot stays BLOCKED after the setting changes → AC-8
- [ ] Linked bug: `finding.bug_report_id` is empty until Slice 3, and the section shows no bug button → AC-12

## Slice 2 (project settings and AI guidance) · added 2026-11-25
_Steps for what Slice 2 built: the three project settings, their copy into each run's snapshot, and the one LLM call that explains the top findings. Run `alembic upgrade head` first (revision `a7d3e9b1c540`)._

### UI / manual
- [ ] Open Project Settings → an "Accessibility checks" group shows the scan switch (on), Release gate (Warn), and Gate threshold (Serious and critical) for a project that never set them → AC-13
- [ ] Set the gate to Block and the threshold to Critical only, save, and reload the page → the values stay → AC-13
- [ ] Gate Block, then run a test case on a page with a critical violation → the Accessibility section shows BLOCKED, and the test's own pass or fail result is unchanged → AC-8
- [ ] Threshold Critical only, then run on a page with only serious violations → PASS → AC-8
- [ ] Turn the scan switch off, then run a test case → no `a11y.json`, and the section says Not scanned (disabled) → AC-8, AC-13
- [ ] With a working `analysis` LLM, run a test case → the top findings say "AI explanation" with a concrete fix. A Needs human review finding says what a person must check, then "If it fails:" and the fix → AC-4
- [ ] Break the `analysis` LLM (for example, a wrong API key), then run → the run still finishes, and every finding says "Explanation (from axe)" → AC-4
- [ ] Token Utilization → the enrichment call is listed under the `accessibility_agent` agent in the Analysis stage → AC-4
- [ ] Tab through the Accessibility checks group → the switch and both selects are reachable, and a screen reader reads the group name and each label → AC-12

### Commands
- [x] `pytest -q backend/tests/test_accessibility.py` → all pass, including the slice 2 settings and enrichment tests → AC-4, AC-5, AC-8, AC-13
- [x] `pytest -q` → the full suite passes (1153 tests on 2026-11-25) → no regressions
- [x] `alembic upgrade head`, then inspect `projects` → `a11y_enabled` (boolean, default true), `a11y_gate_mode` (default `warn`), and `a11y_block_severity` (default `serious`) exist and are not null → AC-13
- [x] `PUT /api/v1/projects/<id>` with `a11y_gate_mode: "strict"` or `a11y_block_severity: "moderate"` → 422. With `a11y_gate_mode: null` → 200 and the value is unchanged → AC-13
- [x] `PUT /api/v1/projects/<id>` as a user without `Projects.edit` → refused → AC-13
- [ ] `GET .../executions/<run>/accessibility` for a run started in block mode, after the project moved to warn → still `BLOCKED` with `gate_mode: "block"` → AC-8

### Value sourcing (Slice 2 rows)
- [ ] Scan on or off: `project.a11y_enabled` AND `accessibility.enabled`. Try all four combinations (project off, `A11Y_ENABLED=false`, both, neither) → only "both on" scans → AC-1, AC-13
- [ ] Gate mode and threshold: the saved execution's `project_config_snapshot` holds `a11y_gate_mode` and `a11y_block_severity` as strings, and the gate matches them, not the current project → AC-8
- [ ] AI explanation and fix: set `A11Y_MAX_LLM_FINDINGS=1` → only the highest impact finding says "AI explanation", and the rest use axe text → AC-4
- [ ] Redaction before the LLM: a page input holding the vault password → the LLM trace (LangSmith, or a logged prompt) shows `[redacted]` and no `value` attribute → AC-5

## Slice 3 (bugs, Jira, and element screenshots) · added 2026-11-26
_Steps for what Slice 3 built: accessibility bug drafts, their Jira labels and screenshot, the screenshot route, and the bug store changes. Run `alembic upgrade head` first (revision `f1c7b3e8d204`). Leave `JIRA_*` unset, or keep `bug_auto_submit` off, so nothing reaches a real Jira._

### UI / manual
- [ ] Run a test case on a page with critical and serious violations → Bug Reports lists one `Accessibility Issue` draft per critical or serious finding, and none for moderate, minor, or needs review findings → AC-6
- [ ] Run the same test case again → no new drafts, and each draft shows 2 occurrences → AC-6
- [ ] In execution details, open a finding → it has an **Open bug report** button, and Enter on it opens that draft in Bug Reports, with the accessibility rule and WCAG line under Triage evidence → AC-6, AC-12
- [ ] Open a critical or serious finding that has a screenshot → the element screenshot loads, with alt text naming the rule → AC-7, AC-12
- [ ] Dismiss one accessibility draft and mark another resolved (Jira webhook), then run again → the dismissed one gets no new draft, and the resolved one gets a new draft → AC-6
- [ ] Set `a11y_block_severity` to Critical only, then run → only critical findings draft → AC-6
- [ ] Submit an accessibility draft → the Jira issue has `accessibility`, `a11y_<rule>`, every `wcag_<criterion>`, the `test_<id>` label, the element screenshot attached, and the "does not certify WCAG conformance" line. The run's own Jira link stays empty → AC-7
- [ ] Turn `bug_auto_submit` on, then run → new accessibility drafts are submitted at once (no confidence threshold) → AC-7
- [ ] axe on the Accessibility section and the Bug Reports detail → no `color-contrast` violations in dark or light theme → AC-12

### Commands
- [ ] `pytest -q backend/tests/test_accessibility_bugs.py` → all 15 pass → AC-6, AC-7, AC-12
- [ ] `pytest -q` → the full suite passes (1168 tests on 2026-11-26) → no regressions
- [ ] `alembic upgrade head`, then inspect `bug_reports` → `execution_id` and `test_case_id` are nullable, `accessibility_report_id` and `failure_category` exist, `ck_bug_reports_has_source` and `ix_bug_reports_category_signature` exist, and old rows have `failure_category` filled → AC-6
- [ ] `GET .../accessibility/reports/<id>/screenshots/<name>` → 200 PNG for a name a finding records. `..%2Fa11y.json`, a name no finding records, or another project's report → 404 → AC-12, AC-15
- [ ] `POST .../executions/<failed run>/bug-report` on a run that also has accessibility bugs → it drafts or returns the run's functional bug, never an accessibility bug (`get_by_execution` leaves them out) → AC-6

### Value sourcing (Slice 3 rows)
- [ ] Bug draft severity: a critical finding → `critical`, a serious finding → `major` → AC-6
- [ ] Bug draft environment: a run bug has `target_url`, `framework`, `browser` from the run snapshot plus `page_url`. A scan bug (no run) uses the project's `target_url` and `framework` → AC-7
- [ ] Bug title context: with a test case, the title names the test case. With none (a scan), it names the page URL, and there is no `test_` label → AC-7
- [ ] Report UI element screenshot: `finding.screenshot_path` is a plain file name in `<run folder>/a11y-shots/`, set only for critical and serious violations, at most `max_element_screenshots` per run → AC-7, AC-12
- [ ] Report UI linked bug: `finding.bug_report_id` is set for each drafted or reused bug, and stays empty when a dismissed bug ignores the repeat → AC-6, AC-12

## Slices 5 and 6 (standalone scan, login, reports, JUnit, docs) · added 2026-11-26

Setup: `alembic upgrade head` (adds `uq_accessibility_reports_active_scan`), start the backend, and pick a project whose target URL opens. For the login steps, use a project marked as a safe test environment with a stored username and password, whose target URL shows the login form.

### UI / manual
- [ ] Project dashboard → **Accessibility scans** card shows **Run accessibility scan** and the list of past scans (or "No scans yet") → AC-12
- [ ] Click **Run accessibility scan** → the dialog lists the target URL, literal object repository pages, and test case pages, all ticked; a wildcard pattern shows disabled with "skipped, a wildcard pattern cannot be opened" → AC-10
- [ ] Untick a page, add `/some/path` → it appears ticked as "added by you"; adding `https://other.example.com/x` shows "Only pages on <origin> can be scanned." and adds nothing → AC-10
- [ ] Tick more than `max_pages` pages → the count says "Untick some pages to continue." and **Start scan** is disabled → AC-10
- [ ] Start a public scan → toast, the list shows **Queued** then **Running**, and it refreshes by itself to a gate badge, page count, and counts → AC-10
- [ ] **View** a finished scan → the report shows gate, counts, violations, needs human review, scanned pages, and the disclaimer; focus moves to the report heading → AC-12
- [ ] A page that redirects to a login form shows "skipped, needs login" in a public scan → AC-11
- [ ] On a project not marked safe, **Include pages behind login** is disabled with an explanation → AC-11
- [ ] On a safe project, start a login scan → the report says "Login: succeeded", and the pages that redirected to login are scanned (listed with no skip reason) → AC-11
- [ ] Change the stored password to a wrong one and scan with login → "Login: failed. Login failed, so pages behind login were not scanned." and those pages stay "skipped, needs login" → AC-11
- [ ] Keyboard only: open the dialog, Tab through every control (focus stays inside the dialog), tick and untick with Space, add a path with Enter, close with Escape → focus returns to **Run accessibility scan** → AC-12
- [ ] A user without `Executions.edit` sees **Run accessibility scan** disabled with a tooltip → AC-15
- [ ] Open a run's HTML report (legacy engine) → an **Accessibility** section after the summary with the gate, counts, findings table, and "An automated scan does not certify WCAG conformance." → AC-14
- [ ] Open the PDF report (legacy engine) → an **Accessibility** page with the gate, counts, findings, and the disclaimer → AC-14
- [ ] Switch to the professional engine and open the HTML and PDF → the same section, plus an **Accessibility** link in the table of contents → AC-14
- [ ] A run of a project with scanning off → the report section says "Accessibility scanning is turned off for this project." → AC-14

### Commands
- [ ] `POST .../accessibility/scans` with `{"paths": ["https://other.example.com/"]}` → `422` → AC-10
- [ ] `POST .../accessibility/scans` with 11 paths (default cap 10) → `422` → AC-10
- [ ] `POST .../accessibility/scans` twice in a row → the second is `409` → AC-10
- [ ] `POST .../accessibility/scans` with `{"paths": [], "include_login": true}` on a project that is not a safe test environment → `409` → AC-11
- [ ] `POST .../accessibility/scans` with `{"paths": []}` → `202`, `status: queued`; the report then scans the target URL only → AC-10
- [ ] `POST .../accessibility/scans` as a user without `Executions.edit` → `403` → AC-15
- [ ] `GET .../accessibility/reports/{id}` with a report id from another project → `404` → AC-15
- [ ] `GET .../accessibility/reports?source=scan&limit=101` → `422`; with `limit=20` → items without `findings`, newest first, plus `total` → AC-15
- [ ] Stale scan: set a queued scan's `created_at` 16 minutes back in the store, then `GET .../accessibility/reports` → it is `failed` with `error: timeout` and `gate_status: NOT_SCANNED`; restarting the server closes it the same way → AC-10
- [ ] Postgres: `INSERT` a second `queued` scan row for a project that has one → unique violation on `uq_accessibility_reports_active_scan` → AC-10
- [ ] Login scan credentials: after a login scan, search the saved report (and the server log) for the stored password → not found → AC-5, AC-11
- [ ] JUnit (legacy `junit_<id>.xml`, professional `junit_<id>.xml`): a run with violations has `testsuite name="accessibility"` with one failing `testcase` per violating rule; a clean run has one passing `testcase`; a not scanned run has one skipped `testcase` with the reason → AC-14

### Value sourcing checks
- [ ] Candidate pages come from `scan_urls` sources: add a literal object repository page and a test case naming a URL → both appear in `scan-pages` with `source` `object_repository` and `test_case` → AC-10
- [ ] Base URL is the project snapshot's `target_url`: change the project target URL, then reopen the dialog → the list and the allowed origin follow the new URL → AC-10
- [ ] Gate settings come from the project at scan start: set `a11y_gate_mode=block`, start a scan with a serious finding, switch to `warn` while it runs → the finished report is `BLOCKED` with `gate_mode: block` → AC-8
- [ ] Credentials come from the vault: change the stored password and rescan with login → the result follows the new password → AC-11
- [ ] Which pages need login comes from the sessionless first pass: a page that redirects to a page with no password field is scanned as is (not marked), and an off origin redirect is `navigation_failed` → AC-11

### Acceptance criteria coverage (this slice)
- AC-10 · dialog, custom path, cap, origin, one active scan, stale timeout steps
- AC-11 · needs login marking, safe environment rule, login success and failure, credential steps
- AC-12 · scans card, report view, keyboard steps
- AC-14 · HTML, PDF (both engines), not scanned, JUnit steps
- AC-15 · permission and other project steps
