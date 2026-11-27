# 0011. AI Accessibility Testing Agent: WCAG scans with AI guidance, bugs, and a release gate

**Date**: 2026-11-25
**Status**: In Progress

## Summary

This spec adds an Accessibility Agent. It checks whether people with disabilities, such as screen reader users and keyboard users, can use the project's application. The agent scans every page a test visits with axe-core (the standard rule engine for WCAG, the web accessibility guidelines), and it can also scan chosen pages on request. The AI explains each problem in plain words and suggests a fix. Serious problems become bug report drafts for Jira, and each run gets a release gate result (pass, warn, or blocked) that CI can read. Phase 1 covers the rule scan only. The keyboard and focus walk comes in a later spec, and visual regression is a separate agent.

## Requirements

**User stories**:
- As a tester, I want every test run to check the pages it visits for accessibility problems, so issues are caught without extra test scripts.
- As a developer, I want each problem explained with the element, the WCAG rule, and a concrete fix, so I can fix it without being an accessibility expert.
- As a QA lead, I want serious problems filed as reviewed bugs, and a gate I can switch to block, so a release with accessibility barriers doesn't ship by accident.
- As a tester, I want to scan chosen pages on demand, including pages behind login, without writing a test case.

**Acceptance criteria**:
- **AC-1**: When a project has `a11y_enabled` on, every test run scans each unique URL path it visits once with axe-core, using the tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, and `wcag22aa`. This applies to Python runner runs (single test case and batch) and to workspace engine runs.
  - **During the test**, the hook only records settled URLs, from `framenavigated` and `history` changes. Paths are deduped, ignoring the query string and hash.
  - **In `afterEach`**, on a hook timeout separate from the test's own, axe opens each recorded path again in the same signed-in browser context.
  - The test's own steps are never slowed or disturbed.
  - Scanning stops at `accessibility.max_pages` (default 10) or once total axe time reaches `accessibility.page_budget_ms` (default 30000). Pages skipped by either limit are listed in `pages[]` with `error=budget`.
  - The raw results land in `ARTIFACTS_ROOT/<run_id>/a11y.json`. A stale file is deleted before each run, as `harvest.json` is. Workspace runs send them as the `a11y` field of the `completed` webhook, capped at 50 nodes per rule per page and 1 MB in total. Snippets are dropped first, then the payload is marked `truncated=true`.
- **AC-2**: A scan failure never changes a run's functional result or fails a webhook. A whole scan failure saves the report with `status=failed`, `error=scan_error`, and `gate_status=NOT_SCANNED`. A failure on one page, for example a page whose CSP blocks axe, only sets `pages[].error` (such as `csp_blocked`) and the other pages still count.
- **AC-3**: Each axe violation becomes a finding with `kind=violation`. Each axe `incomplete` result becomes a finding with `kind=needs_review`, capped at 50 nodes per rule. Findings with the same rule and the same normalized selector are merged into one finding. It keeps up to 20 pages in `pages[]` and counts every one in `occurrences`.
- **AC-4**: The top `accessibility.max_llm_findings` findings (default 20), ordered by impact (critical, serious, moderate, minor, then needs review), get an AI explanation, a code fix, and for `needs_review` findings, guidance on what a person must check. This is one structured LLM call with role `analysis`, agent `accessibility_agent`, and a 60 second timeout. Findings past the cap, and every finding when the LLM call fails, use axe's own `help` and `failureSummary` text with `ai_source=axe_fallback`. Enrichment never fails a run.
- **AC-5**: HTML snippets, axe `failureSummary` text, and selectors are redacted before they are sent to the LLM or saved. The redaction removes vault credentials and test data values, using `BugReportService._secret_patterns` and `_clean`, which become a shared helper. It also drops `value` attributes and caps each snippet at 500 characters.
- **AC-6**: For each violation whose impact is at or above the project's `a11y_block_severity` (default `serious`), a bug report draft is created with category `Accessibility Issue`.
  - One open bug covers one finding signature per project, across pages, test cases, runs, and scans. The signature is stored in `error_signature` and looked up with the new `find_open_a11y_by_signature(project_id, signature)`.
  - A repeat adds an occurrence and still sets the finding's `bug_report_id`.
  - A `dismissed` bug stays closed, and repeats are ignored. A `resolved` bug gets a new draft.
  - `get_by_execution` and the functional `find_open_by_signature` leave out `Accessibility Issue`, so one run can have a functional bug and accessibility bugs.
  - `needs_review` findings never create bugs.
- **AC-7**: Accessibility bugs follow the project's `bug_auto_submit` setting and the existing Jira submit flow (spec 0010), with these extra labels: `accessibility`, `a11y_<rule_id>` (for example `a11y_color-contrast`), and `wcag_<criterion>` for each criterion (for example `wcag_1.4.3`). A bug with no test case gets no `test_` label and uses the page URL as its title context. The element screenshot is attached when one exists.
- **AC-8**: Each report gets a gate status:
  - `off` mode: always `PASS`.
  - `warn` mode: `WARN` when any violation is at or above the threshold, else `PASS`.
  - `block` mode: `BLOCKED` when any violation is at or above the threshold, else `PASS`.
  - Disabled projects and failed scans get `NOT_SCANNED`, with `gate_reason` set to `disabled` or `scan_error`. The gate never silently passes a run that was not scanned.
  - Only `violation` findings at or above the threshold count. `needs_review`, moderate, and minor findings never affect the gate.
  - The gate is computed once, when the report finishes, from the settings saved in the run's `project_config_snapshot` when the run starts. A later settings change does not rewrite old gates. Responses include the `gate_mode` used.
  - **Workspace runs** have no backend side start, so their snapshot is built in `finalize_execution`, before the report is processed (see *Workspace run settings*). Its gate mode and threshold are the ones the engine read from `scan-settings` at run start and echoed back as `a11y_settings` in the `completed` webhook, and the echoed `enabled` decides whether the run counts as scanned. Each echoed field that is missing or not a valid value falls back to the project's value at finalize time, with a warning in the log. When no echo arrives at all (the engine's settings read failed, or an older engine), everything comes from the project at finalize time, so a project with scanning on and no `a11y` field gets `NOT_SCANNED` with `scan_error`, never a pass.
  - A repeated `completed` webhook reuses the snapshot saved the first time, so the gate never changes on retry.
  - The functional `ExecutionResult.status` never changes because of the gate.
- **AC-9**: `GET /api/v1/projects/{id}/accessibility/gate` takes exactly one of `execution_id` or `batch_id`. The batch id is the runs' `ExecutionResult.test_cycle_id`.
  - A batch returns the worst status among its runs (BLOCKED, then WARN, then NOT_SCANNED, then PASS) and lists each run's status.
  - A run with no report counts as `NOT_SCANNED`, and a batch with no reports returns `NOT_SCANNED`.
  - Callers authenticate with a user session that has `Executions.view` and project access, or with `X-Webhook-Secret` (`require_workspace_secret`).
  - An `execution_id` from another project returns 404.
- **AC-10**: A standalone scan:
  - is started with `POST /api/v1/projects/{id}/accessibility/scans`, with `{paths: [...], include_login: bool}`
  - the screen lists `scan_urls()` (the target URL, literal object repository page patterns, and test case URLs) as a checklist with every item ticked. Wildcard patterns show as skipped. The user can untick items and add custom paths under the target URL.
  - the request is capped at `max_pages`. Paths must be on the project target URL's origin (422 otherwise).
  - the report is created at once with `status=queued`, then moves to `running`, then `succeeded` or `failed`
  - only one scan can be queued or running per project (409 otherwise). Postgres enforces this with a partial unique index, and the JSON store with a process lock.
  - a report still queued or running after 15 minutes is marked `failed` with `error=timeout`. This is checked on every read of scan reports and once at startup, so a scan lost in a restart is closed too.
- **AC-11**: With `include_login` on:
  - The scan first visits every page with no session. Any page that redirects to a URL with a visible password field is marked `requires_login`.
  - It then opens the target URL, finds the login form (recon's `_looks_like_login_page` and field token matching), fills the vault username and password, and submits.
  - The login succeeds when the URL path changed, no password field is visible, and no `[role=alert]` appears.
  - On success it scans the `requires_login` pages again. On failure it records `login_status=failed` and lists those pages with `error=requires_login`.
  - `include_login` requires the project's `is_safe_test_environment` (409 otherwise). Public page scans work for any project.
- **AC-12**: The execution details screen shows an Accessibility section with:
  - the gate badge
  - counts by impact
  - findings grouped as violations and needs human review, each with rule, WCAG criteria, pages, element, AI explanation, fix, and a link to its bug report
  - a "not scanned" state with its reason

  The project page offers **Run accessibility scan**, with the page checklist and an include login option, plus a list of past scan reports. Every control works by keyboard and has an accessible name.
- **AC-13**: Project settings expose `a11y_enabled` (default on), `a11y_gate_mode` (`off`, `warn`, or `block`, default `warn`), and `a11y_block_severity` (`critical` or `serious`, default `serious`). They are `Literal` typed, so any other value returns 422, and a null in a partial update leaves the value unchanged. Changing them needs `Projects.edit`, the existing `update_project_settings` route. The three values are copied into `project_config_snapshot` when each run starts. For workspace runs, "when the run starts" means the engine's `scan-settings` read, echoed back at finalize (AC-8).
- **AC-14**: HTML and PDF reports (legacy and professional engines) load the run's report with `get_accessibility_report_by_execution` and include an Accessibility section with the gate, counts, and a findings table, or the not scanned state with its reason. It always carries the line "An automated scan does not certify WCAG conformance." JUnit output adds a `testsuite name="accessibility"` with one `testcase` per rule that fired, failing for violations. A scan with no violations gets a single passing `testcase`.
- **AC-15**: Reports are stored through the repository (JSON and PostgreSQL) and kept until the project is deleted. The JSON store removes them on project delete, and Postgres cascades. There is no pruning. Read routes need `Executions.view`, and starting a scan needs `Executions.edit`, both with project access. A report from another project returns 404.

## Decision

**Chosen option**: a separate `AccessibilityAgent` graph node backed by an `AccessibilityService`. One shared browser hook (`a11y-scan-session.ts`, built on `@axe-core/playwright`) runs on both execution paths and in a fixed template standalone scan. Findings are stored in a new `accessibility_reports` record and feed the existing `BugReportService` and Jira flow.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

**Data model sketch**:

`accessibility_reports` (new; 1 project : N reports; 1 execution : 0..1 report)

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `project_id` | UUID | no | FK `projects.id`, cascade delete |
| `source` | `execution` \| `scan` | no | |
| `execution_id` | UUID | yes | unique when set |
| `batch_id` | str | yes | indexed; copied from `ExecutionResult.test_cycle_id` |
| `gate_mode` | `off` \| `warn` \| `block` | no | the mode used, from the run snapshot |
| `test_case_id` | UUID | yes | set when `source=execution` |
| `status` | `queued` \| `running` \| `succeeded` \| `failed` | no | |
| `error` | str | yes | `scan_error`, `login_failed`, `timeout` |
| `login_status` | `not_requested` \| `succeeded` \| `failed` | no | |
| `wcag_tags` | JSON list[str] | no | |
| `pages` | JSON list | no | `{url, title, scanned_at, requires_login, error}`; `error` is one of `budget`, `csp_blocked`, `requires_login`, `navigation_failed`, or null |
| `truncated` | bool | no | default false; true when the webhook payload was cut |
| `findings` | JSON list[AccessibilityFinding] | no | |
| `counts` | JSON | no | `{critical, serious, moderate, minor, needs_review}` |
| `gate_status` | `PASS` \| `WARN` \| `BLOCKED` \| `NOT_SCANNED` | no | |
| `gate_reason` | str | yes | `disabled`, `scan_error` |
| `created_by` | UUID | yes | user who started a scan |
| `created_at` | datetime | no | index `(project_id, created_at)` |
| `finished_at` | datetime | yes | |

`AccessibilityFinding` (embedded in `findings`):
- `signature` (sha256 of `a11y:` + rule id + normalized selector)
- `kind` (`violation` \| `needs_review`)
- `rule_id`, `impact`, `wcag_criteria[]`, `help`, `help_url`
- `selector`, `html_snippet` (redacted, at most 500 characters)
- `pages[]` (at most 20), `occurrences`, `failure_summary` (redacted)
- `ai_explanation`, `ai_fix`, `ai_source` (`llm` \| `axe_fallback`)
- `screenshot_path?`: a file name relative to the run folder, served only through the screenshot route
- `bug_report_id?`

To normalize a selector, take axe's `target`, join nested frame and shadow parts with ` >> `, and strip `:nth-child(n)` indexes from tables and lists only, so repeated rows merge.

`projects` (changed): `a11y_enabled` bool (default true), `a11y_gate_mode` (default `warn`), and `a11y_block_severity` (default `serious`).

`bug_reports` (changed):
- `execution_id` and `test_case_id` become nullable. Existing rows keep their values, and the migration only relaxes nullability.
- New nullable `accessibility_report_id`. At least one of `execution_id` or `accessibility_report_id` must be set. Postgres enforces this with a check constraint, and the model with a validator.
- Code that assumes `test_case_id` is set must handle null: `bug_report_service.py` around lines 395, 519, and 588, the Bug Reports list, and the chat bug tools.
- `BugOccurrence.execution_id` becomes nullable, and occurrences gain `accessibility_report_id`.
- New index `(project_id, failure_category, error_signature)`.

`WorkflowState` (changed): `accessibility_results: list[AccessibilityReport]`, written only by AccessibilityAgent. It is a list because one graph run can hold several `execution_results`, with one item for each execution.

`project_config_snapshot` (changed): also records `a11y_enabled`, `a11y_gate_mode`, and `a11y_block_severity` when the run starts. Workspace runs now get a full snapshot too (the same keys as graph runs), written once by `finalize_execution`. No schema change: the column already exists, and both repositories already keep the first snapshot saved.

Completed webhook payload (changed): new optional field `a11y_settings: {enabled: bool, gate_mode: str, block_severity: str}`, the values the engine read from `scan-settings`. The engine sends it whenever that read succeeded, including when `enabled` is false. When the read failed, it sends `a11y_settings_error: str` instead, for the log only.

**Processing flow (one service, three callers)**:
- `AccessibilityService.process(project_id, execution_result, raw) -> AccessibilityReport` does all the work: normalize, dedupe, redact, enrich, gate, and upsert. It is called by AccessibilityAgent (graph runs), `ExecutionService._triage` (batch), and `ExecutionService.finalize_execution` (the workspace webhook).
- The upsert is keyed on `execution_id`, so a retried webhook replaces the report and never duplicates it.
- Bug drafting runs after it: BugReportingAgent on graph runs, and the same `BugReportService.draft_from_accessibility` call in `_triage` and `finalize_execution`.
- Where `raw` comes from: graph and batch runs use `read_a11y_file(ARTIFACTS_ROOT / run_id)`, a twin of `read_harvest_file`, where `run_id` is the test case id the runner already uses. Workspace runs use the webhook's `a11y` field. Missing raw data with scanning enabled means `scan_error`. Scanning disabled means `disabled`.

**Workspace run settings** (where a workspace run's gate settings come from):
Engine side:
1. The engine reads `GET /api/v1/accessibility/scan-settings?project_id=...` once, at the top of `ExecutionEngine.run()`: right after `startExecution`, before the `started` webhook, and outside the retry loop. Then every `completed` send in `run()`, on success and on every error path, can carry the result.
   - The response carries `enabled` (the project's `a11y_enabled` AND `accessibility.enabled`, the engine's final "scan or not" answer), `gate_mode`, `block_severity`, and the caps.
   - Any failure (no or invalid `projectId`, timeout of 2 seconds, non 200, bad JSON) means scanning is off for that run, the same rule as harvest.
2. Every `completed` webhook sends either `a11y_settings: {enabled, gate_mode, block_severity}` (unchanged from the response) or, when the read failed, `a11y_settings_error: "<reason>"` (`timeout`, `http_<status>`, `bad_json`, `no_project`). `a11y_settings_error` is for the log only and never changes the gate.

Backend side, in `finalize_execution`, in this order:
3. Right after the `ExecutionResult(...)` is built, and before the failure triage (step 0), resolve the snapshot. Triage and bug drafting read `result.project_config_snapshot` (`bug_report_service.py` around line 536), so it must be set first.
   - **Saved snapshot wins.** Load `self.repo.get_execution_result(project_id, exec_id)`. If it has a `project_config_snapshot` (a retried webhook, or a row saved by any other path), use it as is. Skip the project lookup and the overlay. If the read raises, roll back the session and treat it as no saved row.
   - **Otherwise build it.** Load `self.repo.get_project(project_id)`. If that raises, roll back the session and treat the project as missing. If the project exists, use `capture_project_snapshot(project)`. On `PROJECT_NOT_READY`, use `capture_project_snapshot(project, require_ready=False)` and log a warning.
   - `require_ready=False` skips `require_ready_project` and every readiness check, including the duplicate URL and unsupported framework checks. It fills the same keys from the raw project: `project_id` from `project.id`, `target_url` as `normalize_target_url(project.target_url) or ""`, `framework` as `normalize_framework(project.framework) or ""` (no `"playwright"` default), `credential_reference` as its value or `""`, and the three a11y fields as today. Every value stays a string, matching `dict[str, str]`.
   - **Overlay the echo** (only on a newly built snapshot): `gate_mode` replaces `a11y_gate_mode` only when it is exactly `off`, `warn`, or `block` (a string, lowercase, not trimmed). `block_severity` replaces `a11y_block_severity` only when it is exactly `critical` or `serious`. A bad or missing field keeps the project value and logs `a11y_settings field ignored: <name>`. A bad base value is still safe, because `gate_settings` falls back to its defaults.
   - `enabled` is **not** written into the snapshot. `a11y_enabled` keeps the project's own value, so the snapshot never claims a project setting the project didn't have.
4. **Missing project** (deleted during the run): the snapshot stays null, and accessibility processing and accessibility bug drafting are skipped with a warning log. No report is saved, and the gate route already returns 404 for that project.
5. After `save_execution_result`, read the result back with `get_execution_result` and pass that persisted copy to `AccessibilityService.process`, then to `draft_from_accessibility`, then to `emit_execution_completed`. The gate therefore always uses the snapshot that was stored first. `process` needs no workspace special case.
6. **The scan decision:** when `a11y_settings.enabled` is a JSON boolean (`true` or `false`; strings and numbers don't count), finalize passes it as `process(..., enabled=<value>)`, so the global switch isn't checked a second time at finalize. Otherwise it passes nothing, and `process` uses `scan_enabled(snapshot)` as today. A missing `a11y` field with scanning on gives `scan_error`.
7. **Logging:** with no `a11y_settings`, finalize logs `a11y settings read failed in engine: <a11y_settings_error>` when that field is present, else `no a11y_settings in completed webhook (older engine?)`. Either way the gate reason stays `scan_error` when scanning is on.
8. Rows saved before this change, whose snapshots have no `a11y_*` keys, are gated with the built in defaults (`warn`, `serious`, enabled). That's acceptable, because those runs were never scanned under this path.

**Standalone scan run**:
- `AccessibilityScanService` writes `scan-input.json` (paths, origin, caps, `include_login`) to `ARTIFACTS_ROOT/scan_<report_id>/`.
- It runs the fixed template spec with `PlaywrightRunner.run(script, run_id="scan_<report_id>", project_id, target_url)` and `A11Y_SCAN=1`. Credentials arrive as the usual `TARGET_USERNAME` and `TARGET_PASSWORD` env vars.
- It reads `a11y.json` from the same folder and calls `AccessibilityService.process` with no execution.
- The template's first build task confirms that `PlaywrightRunner.run` works without a test case or ExecutionResult. If it doesn't, the task adds that support before anything else.
- The scan runs in a background thread wrapped with `run_in_context`.

**State transitions**:
- standalone scan: `queued` → `running` → `succeeded` \| `failed`. A run older than 15 minutes moves to `failed` with error `timeout`.
- execution report: created already `succeeded` or `failed`.
- `gate_status` is computed once, when the report reaches `succeeded` or `failed`.

**API surface**:
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/v1/projects/{id}/accessibility/scans` | POST | `paths: list[str]` (req, may be empty, which means target URL only), `include_login: bool` (opt, default false) | `report_id`, `status=queued` | `Executions.edit` + project access | 409 scan running, 409 login without safe env, 422 off origin or over cap, 404 project |
| `/api/v1/projects/{id}/accessibility/scan-pages` | GET | none | `pages: [{url, source, skipped_reason?}]` from `scan_urls()` | `Executions.view` + project access | 404 |
| `/api/v1/projects/{id}/accessibility/reports` | GET | `source?`, `limit` (default 20, max 100), `offset` | paged report summaries (no findings) | `Executions.view` + project access | 404 |
| `/api/v1/projects/{id}/accessibility/reports/{report_id}` | GET | none | full report with findings | `Executions.view` + project access | 404 |
| `/api/v1/projects/{id}/executions/{execution_id}/accessibility` | GET | none | full report, or `{gate_status: NOT_SCANNED, gate_reason}` | `Executions.view` + project access | 404 |
| `/api/v1/projects/{id}/accessibility/reports/{report_id}/screenshots/{name}` | GET | none | the image file | `Executions.view` + project access | 404 for a missing file, or a name with `/`, `\`, or `..` |
| `/api/v1/projects/{id}/accessibility/gate` | GET | `execution_id` or `batch_id` (exactly one) | `gate_status`, `gate_reason`, `gate_mode`, `runs: [{execution_id, gate_status}]` | `Executions.view` session, or `X-Webhook-Secret` (`require_workspace_secret`) | 422 neither or both given, 404 |
| `/api/v1/accessibility/scan-settings` | GET | `project_id` (req) | `enabled`, `gate_mode`, `block_severity`, `wcag_tags`, `max_pages`, `page_budget_ms`, `max_element_screenshots`, `max_nodes_per_rule` | `X-Webhook-Secret` | 404 project |
| `/api/v1/projects/{id}/executions/webhook` (changed) | POST | `completed` event gains optional `a11y`, `a11y_settings`, and `a11y_settings_error` | unchanged | `X-Webhook-Secret` (unchanged) | never fails because of either field |

The workspace engine reads the last route the same way it reads `/api/v1/object-repository/harvest-settings`. The project settings fields go through the existing project update route.

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Execution scan | which pages | settled URLs recorded from `framenavigated` and `history` changes during the test, deduped by path (ignoring the query string and hash), then scanned again in `afterEach` |
| Execution scan | raw results | `ARTIFACTS_ROOT/<run_id>/a11y.json` (graph and batch), or the webhook `a11y` field (workspace) |
| Report | project, test case, batch | `execution_result.project_id` (from the caller), `execution_result.test_case_id`, `execution_result.test_cycle_id` |
| Execution scan | WCAG tags, caps | `accessibility` config section (`get_settings().accessibility`), passed as env vars `A11Y_SCAN`, `A11Y_TAGS`, `A11Y_MAX_PAGES`, `A11Y_PAGE_BUDGET_MS`, `A11Y_MAX_SHOTS` |
| Execution scan | scan on or off | `project.a11y_enabled` AND `accessibility.enabled`. Workspace runs: the engine scans when the `scan-settings` `enabled` value it read at run start is true (off if that read failed). Finalize passes the echoed boolean as `process(enabled=...)`, else uses `scan_enabled(snapshot)` |
| Workspace run | `project_config_snapshot` | the saved snapshot if one exists, else `capture_project_snapshot(self.repo.get_project(project_id))` at finalize (or `require_ready=False` when not ready), with `a11y_settings.gate_mode` and `block_severity` overlaid field by field. Null when the project is gone (accessibility processing skipped) |
| Finding | impact, rule, help, help_url, selector, html | axe result node |
| Finding | `wcag_criteria` | derived from axe tags (`wcag143` becomes `1.4.3`) |
| Finding | AI explanation and fix | LLM `AccessibilityEnrichment` response, else axe text |
| Bug draft | severity | from impact: critical becomes `critical`, serious becomes `major` (moderate and minor never draft) |
| Bug draft | environment | report page URL plus the run's `project_config_snapshot` (normalized URL, framework, browser). For workspace runs, `target_url` and `framework` are the values at finalize, not at run start |
| Gate | mode and threshold | `project_config_snapshot.a11y_gate_mode` and `a11y_block_severity` from the run start. Workspace runs: the echoed `a11y_settings` values in the snapshot built at finalize, falling back per field to the project at finalize time. Standalone scans read the project at scan start. |
| Report UI | element screenshot | `finding.screenshot_path` served by the screenshot route |
| Batch gate | run list | reports with that `batch_id` |
| Standalone scan | candidate pages | `ObjectRepositoryScanService.scan_urls(project)` in `backend/services/object_repository_scan.py`. Literal patterns are joined to the target origin, and wildcard patterns are returned as `skipped_reason=wildcard`. The `source` is `target_url`, `object_repository`, or `test_case`. |
| Standalone scan | which pages need login | redirect to a URL with a visible password field during the first, sessionless pass |
| Standalone scan | credentials | `VaultService.get_credentials(project_id)`, passed as `TARGET_USERNAME` and `TARGET_PASSWORD` by `PlaywrightRunner` |
| Standalone scan | base URL | `capture_project_snapshot(project).target_url` |
| Report UI | linked bug | `finding.bug_report_id` |

**Key invariants**:
- A scan or enrichment error never changes `ExecutionResult.status` or fails a webhook. axe never runs while the test's own steps run.
- `bypassCSP` is never turned on. A page that blocks axe is recorded and skipped.
- Only AccessibilityAgent writes `accessibility_results`, and only BugReportingAgent writes `bug_report_ids`.
- At most one report per `execution_id`, and at most one queued or running scan per project.
- Every execution that reaches the gate has a `project_config_snapshot` unless its project no longer exists. A snapshot is written once and never replaced, on every path.
- A bad `a11y_settings` field never fails the webhook. It only falls back to the project value.
- The backend trusts `a11y_settings` from any caller holding `X-Webhook-Secret`, the same trust it already gives that caller for the run's result, `a11y`, and `harvest`. A compromised engine could send `gate_mode=off`, and this spec accepts that.
- Inside `finalize_execution`, the snapshot is resolved before triage, and accessibility processing reads the persisted copy after the save.
- At most one open accessibility bug per `(project_id, signature)`.
- No password, vault value, test data value, or input `value` attribute is ever stored in a report or sent to the LLM.
- Standalone scans only visit URLs on the project target URL's origin.

**Security model**:
- Reads need `Executions.view`, and starting a scan needs `Executions.edit`, both with project access (spec 0009 pattern).
- The gate route also accepts the workspace secret, for CI.
- Project settings changes use the existing project settings permission.
- Login scans run only on projects marked as a safe test environment, because they submit real credentials.
- Starting a scan writes an audit entry (`SCAN`, `ACCESSIBILITY`), the same way the object repository scan does.
- Credentials travel only as runner env vars and are never logged.

**Configuration required**:
- `accessibility:` section in `backend/config/default_config.yaml`: `enabled` (true), `wcag_tags`, `max_pages` (10), `page_budget_ms` (30000), `max_llm_findings` (20), `llm_timeout_seconds` (60), `max_element_screenshots` (10), `max_nodes_per_rule` (50), `max_pages_per_finding` (20), `webhook_payload_max_bytes` (1000000), `scan_stale_after_seconds` (900). `wcag_tags` is checked against known axe tags when the config loads, so a bad value fails at startup, not during a run.
- Env overrides: `A11Y_ENABLED`, `A11Y_MAX_PAGES`, `A11Y_PAGE_BUDGET_MS`, `A11Y_MAX_LLM_FINDINGS`.
- No new secrets.

**Critical test scenarios**:
- Happy path: a test run visits a page with an image that has no alt text and an unlabeled input. `a11y.json` holds both violations, the report shows them with AI text, a serious one creates one bug draft, and the gate in `warn` mode returns `WARN`. Verifies **AC-1**, **AC-3**, **AC-4**, **AC-6**, **AC-8**
- Failure case: axe throws during the run. The test still passes, the report says `NOT_SCANNED` with `scan_error`. With the LLM failing, findings use `axe_fallback`. Verifies **AC-2**, **AC-4**
- Dedupe: the same element fails on 3 pages in 2 runs. There is one finding per report and one bug with 2 occurrences. Verifies **AC-3**, **AC-6**
- Batch gate: one run BLOCKED and others PASS. The batch gate is BLOCKED. Verifies **AC-9**
- Workspace gate source: a `completed` webhook with `a11y_settings.gate_mode=block` and a serious violation, sent after the project moved to `warn`, gives BLOCKED with `gate_mode=block`. The same webhook with `gate_mode="strict"` falls back to `warn`. A webhook with no `a11y_settings` and no `a11y` for a project with scanning on gives `NOT_SCANNED` with `scan_error`. Reposting the first webhook after changing the project keeps BLOCKED. Verifies **AC-8**, **AC-13**
- Standalone scan: a login scan on a non safe project gets 409, a second scan while one runs gets 409, and a path on another host gets 422. Verifies **AC-10**, **AC-11**
- Auth/permission: a user without `Executions.edit` is refused when starting a scan, and a report from another project returns 404. Verifies **AC-15**

## Build plan

Tracer Bullet: the first slice carries one test run's scan from the browser through the agent, storage, API, and UI. Later slices thicken it.

**Slice 1: thin thread (one run, end to end)**
1. Add `@axe-core/playwright` to `backend/playwrightt` (pnpm). Write `lib/a11y-scan-session.ts`, modelled on `object-harvest-session.ts`. It does two things:
   - records settled URLs during the test (from `framenavigated` and `history` changes, deduped by path)
   - scans them in `afterEach` within the page and time caps, writing `a11y.json`, catching each page's errors, and recording skipped pages

   Satisfies **AC-1**, **AC-2**, **AC-3**
2. Hook it into the `PlaywrightRunner` injected wrapper behind `A11Y_SCAN=1`, with `_a11y_env()` next to `_harvest_env`. Delete a stale `a11y.json` before each run. Add `read_a11y_file`. Add the `accessibility` config section and `AccessibilityConfig` in `settings.py`, checking tags when the config loads. Satisfies **AC-1**
3. Add `backend/models/accessibility.py`, the `accessibility_results` state field, the repository methods (JSON first), and the Postgres table and its migration. Satisfies **AC-15**
4. Write `AccessibilityService.process` (normalize, dedupe with caps, redact through the shared redaction helper, axe fallback text, gate, upsert on `execution_id`) and `AccessibilityAgent`, with no LLM yet. Add the node in `build_graph` between `execution_analysis_agent` and `bug_reporting_agent`, and inject it in `run_execution_and_stream`. Satisfies **AC-2**, **AC-3**, **AC-5**, **AC-8**
5. Add the routes `GET .../executions/{eid}/accessibility` and `GET .../accessibility/gate` (execution only), and the Accessibility section in `renderHistoricalExecution`. Satisfies **AC-8**, **AC-12**, **AC-15**

**Slice 2: project settings and AI guidance**
6. Add the project fields (model, `Literal` typed schemas, both repositories, migration), copy them into `project_config_snapshot`, and add the settings form. The scan only runs when enabled, and the gate uses the snapshot. Satisfies **AC-8**, **AC-13**
7. Add LLM enrichment: the `AccessibilityEnrichment` response model, the prompt `backend/prompts/agents/accessibility/roles_and_responsibilities.md`, the cap, the timeout, the fallback, and the token usage label. Satisfies **AC-4**, **AC-5**

**Slice 3: bugs and Jira**
8. Change `bug_reports` (nullable ids, `accessibility_report_id`, check constraint, index, migration) and fix the call sites that assume `test_case_id` is set. Add `find_open_a11y_by_signature`, and leave `Accessibility Issue` out of `get_by_execution` and the functional signature lookup. Add `BugReportService.draft_from_accessibility` with signature dedupe per project, the dismissed and resolved rules, and the label set. Extend `BugReportingAgent` to loop over `accessibility_results`. Satisfies **AC-6**, **AC-7**
9. Add element screenshots for critical and serious nodes (capped, skipping any that fail) in the scan session, plus the screenshot route with path traversal checks, and attach them to bugs. Satisfies **AC-7**, **AC-12**

**Slice 4: every execution path and the batch gate**
10. Call `AccessibilityService.process` and the bug drafting from `ExecutionService._triage` (batch) and from `finalize_execution` (workspace webhook, reading the `a11y` field). Hook the scan session into `execution-engine.ts` next to `startHarvest`, with the payload caps, and add the `scan-settings` route. Satisfies **AC-1**, **AC-2**
10b. Give workspace runs their gate settings (see *Workspace run settings*):
   - Add `gate_mode` and `block_severity` to `scan-settings`.
   - Move the engine's settings read to the top of `run()`, outside the retry loop, and send `a11y_settings` or `a11y_settings_error` on every `completed`.
   - Add `require_ready=False` to `capture_project_snapshot`, with the field rules above.
   - In `finalize_execution`, resolve the snapshot before triage (saved snapshot first, else build and overlay), skip accessibility for a missing project, and pass the persisted result and the echoed `enabled` to `process` after the save.

   Satisfies **AC-2**, **AC-8**, **AC-13**
11. Add `batch_id` support (from `test_cycle_id`) to the gate route. Satisfies **AC-9**

**Slice 5: standalone scan (include login comes last, so it can be cut without affecting the rest)**
12. Confirm or add `PlaywrightRunner.run` support for a spec with no test case. Add `WorkflowOperation.ACCESSIBILITY_SCAN` and its supervisor routing. Write `AccessibilityScanService`:
    - the template spec, reading `scan-input.json`
    - persisted status
    - the stale check on read and at startup
    - one scan per project (partial unique index, JSON lock)
    - the audit entry

    Public pages only for now. Satisfies **AC-10**
13. Add the routes `POST .../accessibility/scans`, `GET .../scan-pages`, and `GET .../reports[/{id}]`, plus the project page scan dialog (checklist, custom path) and the report list. Satisfies **AC-10**, **AC-12**, **AC-15**
13b. Add `include_login`: the sessionless first pass that marks `requires_login`, the generic login using recon's form detection, the success check, the rescan, the safe environment check, and the dialog checkbox. Satisfies **AC-11**

**Slice 6: reports and docs**
14. Add the professional template component `accessibility_section.html`, the legacy HTML and PDF section, and the JUnit accessibility testsuite. Satisfies **AC-14**
15. Update `docs/agents.md`, `docs/api.md`, and `CHANGELOG.md`, and add `docs/features/accessibility-agent.md`. Satisfies **AC-12**, **AC-14** (documentation rule in AGENTS.md)

## Consequences

**Positive**:
- Every run gets accessibility coverage with no new test scripts, and it reuses the existing login, artifacts, Jira, and reports.
- One axe implementation serves the runner, the workspace engine, and standalone scans.
- The gate gives CI a clear signal without changing functional results.

**Negative / tradeoffs**:
- Runs take longer: about 1 to 3 seconds per scanned page (capped at 30 seconds in `afterEach`, outside the test's own timeout), plus one LLM call per run.
- Pages are scanned as they look when reopened, not as the test left them. Popups, modals, and partly filled forms the test opened are not scanned in Phase 1.
- `bug_reports` loses its strict "every bug has a run and a test case" rule. Code that assumes `test_case_id` is set (the list view, Jira labels, chat tools) must handle null.
- The generic login form fill will not handle multi step or SSO logins. Those projects get `login_status=failed` and a public only scan until a later spec adds a better login.
- Automated rules catch only part of WCAG. A `PASS` gate is not conformance, and the UI and reports must say so.
- Sending HTML snippets to the LLM provider sends page structure outside the system, though it is redacted.
- For workspace runs, the non accessibility snapshot fields (`target_url`, `framework`, `credential_reference`) are read at finalize, not at run start. A project edited during a run records the new values. Only the three a11y values are true run start values.
- The backend trusts the echoed values from any caller holding the workspace secret. That is the same trust it already gives that caller for the run's results, but a buggy engine could send the wrong mode.
- A brief settings outage (the 2 second read timeout) makes the affected runs on a scanning project `NOT_SCANNED` with `scan_error`, so a `block` gate can't block them. The log tells an outage from an old engine, but the gate reason doesn't.
- A project deleted during a workspace run gets no accessibility report.
- An older engine build that sends no `a11y_settings` and no `a11y` makes every run on a scanning project `NOT_SCANNED` with `scan_error` until the engine is updated. That is loud on purpose.

**Neutral**:
- Two migrations: the new table plus project columns, and the bug report changes.
- `a11y.json` joins `harvest.json` in the run folder.
- The keyboard and focus walk (Phase 2) and the Visual Regression Agent (Phase 3) will reuse `a11y-scan-session.ts` hooks.

## Follow-up

- [ ] Phase 2 spec: keyboard and focus walk (Tab order, unreachable controls, focus traps, missing focus outline).
- [ ] Phase 3 spec: Visual Regression Agent (baselines, approval step, screenshot diffs).
- [ ] Later: SARIF export, scheduled scans, and change aware page selection.
- [ ] For `/sync`: record in root `AGENTS.md` under `Declined:` the axe-core and accessibility Agent Skills (claude-dev-suite@axe-core and others) and the Deque axe MCP server.
- [ ] Check the current `@axe-core/playwright` version and its `wcag22aa` tag support at install time.
