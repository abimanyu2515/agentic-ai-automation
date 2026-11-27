# 0012. Give batch runs their own batch id

**Date**: 2026-11-26
**Status**: In Progress

## Summary

Today a batch run stores its batch id in `test_cycle_id`, the field that also means "this run belongs to a real test cycle". PostgreSQL only keeps a `test_cycle_id` that matches a real cycle, so it throws batch ids away, and the accessibility batch gate and the batch report then can't find the batch. This spec adds a separate `batch_id` on every run, so a run can belong to a batch, a real cycle, both, or neither. Backend batches and workspace batches both set it, old JSON runs are moved over once, and every batch reader (gate, batch report, UI picker) switches to it.

Update (2026-11-26): the UI has only a Bearer token (no cookie), so it now fetches the guarded batch report and opens it as a blob page, with every report value escaped. And the gate answers `200` with `in_progress: true` for a backend batch that is running but has no saved run yet, so CI can tell "not saved yet" from "wrong id".

Update 2 (2026-11-26): until now no UI control ever started a backend batch, so AC-9's "the UI starts a batch in the selected cycle" had nothing to build on. The test cases view now gets a second button, "Run Selected on Backend", beside the existing "Run Selected Batch" (which still opens the Next.js workspace). It generates any missing scripts first, starts the batch in the selected cycle, and takes you to the live observer, which follows the batch stream and links to the batch report when it ends (AC-15 to AC-17).

## Requirements

**User stories**:
- As a CI pipeline, I want to read the accessibility gate of a batch I started, on either store, so that a release is blocked or passed on real results.
- As a tester, I want to start a batch inside a test cycle, so that the runs count toward that cycle and still group as one batch.
- As a tester, I want the batch picker to show one entry per real batch, so that a test cycle with many runs isn't shown as one giant batch.
- As a tester, I want to run the selected test cases on the backend from the test cases view and watch the batch progress live, so that I get a batch with a `batch_id` (and a gate CI can read) without opening the workspace.

**Acceptance criteria**:
- **AC-1**: `POST /api/v1/projects/{id}/batches/execute` saves every run of the batch with the same `batch_id`, equal to the `batch_id` the response returns. Reading a run back returns that `batch_id` on both the JSON store and PostgreSQL.
- **AC-2**: The batch start takes an optional `?test_cycle_id=<uuid>`. When given, every run of the batch is saved with that `test_cycle_id`. A cycle that doesn't exist, or whose release belongs to another project, returns `404 Test cycle not found` and queues nothing. Without it, `test_cycle_id` is null. `test_cycle_id` never holds a batch id.
- **AC-3**: A workspace run of several selected cases (`runBatchSequentially`) makes one batch id per run of the batch, and every `completed` webhook of that batch carries it as `batch_id`. The workspace sends `test_cycle_id` only when a real cycle was selected, and no longer invents a random one. A single case run sends no `batch_id`.
- **AC-4**: The workspace engine keeps the browser context open between the cases of one `batchId`, and closes it after a run with no `batchId` or when the `batchId` changes. `testCycleId` no longer affects context reuse.
- **AC-5**: `finalize_execution` saves a `batch_id` that isn't a valid UUID as null and logs a warning. A repeated webhook for the same execution keeps the `batch_id` saved first. A `test_cycle_id` that matches no cycle of this project is saved as null with a warning, on both stores.
- **AC-6**: `GET /api/v1/projects/{id}/accessibility/gate?batch_id=` finds the batch's runs by `ExecutionResult.batch_id`, and an accessibility report gets its `batch_id` from `ExecutionResult.batch_id`. A batch run on PostgreSQL is found and listed. A `batch_id` that isn't a UUID returns 404. So does one that matches no run or report of this project and has no backend event log for this project (see AC-12, AC-13).
- **AC-7**: The gate response has `in_progress`. It is `true` while this project's backend batch event log exists and its last event isn't `Completed` or `Error`. The log is `active_batch_events`, tagged with its project id when the batch starts. It is `false` otherwise: for workspace batches, unknown logs after a restart, a log started by another project, and the `execution_id` gate.
- **AC-8**: `GET /api/v1/projects/{id}/batches/{batch_id}/html` builds the report from the runs with that `batch_id` in this project. It requires `Executions.view` and project access through the Bearer token. Another project's batch, or a batch with no runs, returns 404. The executions UI's "View HTML Report" for a batch works like this:
  1. On click, it opens a blank tab synchronously, so popup blockers allow it.
  2. It fetches the report as text through the shared `request` helper, which refreshes the token once on a 401.
  1a. If `window.open` returns `null` (popup blocked), it shows the toast "Allow pop ups to view the report" and stops before fetching. Otherwise it writes "Loading report…" into the blank tab.
  3. It inserts `<base href="{location.origin}/">` and `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'">`. They go right after the first `<head…>` tag, matched without regard to case. If there is none, they go after `<html…>`, else at the very start. Screenshots and video load, and no script can run.
  4. It points the tab at a blob URL, which it revokes after 60 s (that only stops a reload, because the page has loaded by then).
  If the fetch fails, the tab closes and an error toast shows the error message (the server's `detail`). The toast is skipped when the helper already showed one (`error.shown`, the 403 case). A 401 after a failed refresh signs the user out as usual. The per run HTML, PDF and JUnit buttons stay on `window.open`. The route returns 404 only for the "No executions found" `ValueError`. Any other error is logged and returns 500 without its message.
- **AC-9**: The UI starts a backend batch with the current test cycle's id when one is selected (the control is specified in AC-15). The executions UI's batch picker groups runs by `batch_id`, and runs with a `test_cycle_id` but no `batch_id` are listed as single runs. The test cycle view shows backend batch runs started in that cycle.
- **AC-10**: The JSON store runs a migration once, guarded by a stored marker. For each run whose `test_cycle_id` matches no `test_cycles` row, the migration moves that id into an empty `batch_id` and sets `test_cycle_id` to null. Runs with a real cycle id are untouched, and a second start changes nothing. The Alembic migration adds the nullable `executions.batch_id` column and the `(project_id, batch_id)` index, and its downgrade removes both.
- **AC-11**: Execution responses (list and detail) include `batch_id`. The chat `run_batch` action saves its runs with a `batch_id` and no cycle.
- **AC-12**: A batch with no saved run or report, whose event log for this project is still running, gets `200` from the gate. The body is `gate_status: NOT_SCANNED`, `gate_reason: batch_running`, `gate_mode: null`, `runs: []`, `in_progress: true`. Once a run or report is saved, the normal batch gate (AC-6) applies, and `in_progress` stays `true` until the last event ends.
- **AC-13**: A batch with no saved run or report, whose event log for this project ended (`Completed` or `Error`), gets `200` from the gate. The body is `gate_status: NOT_SCANNED`, `gate_reason: batch_no_runs`, `gate_mode: null`, `runs: []`, `in_progress: false`. When the log is gone (after a restart), the same request returns 404. Another project asking about either batch gets 404.
- **AC-14**: Every interpolated value in the batch HTML is HTML escaped. That includes case title, step title, action, observation, result, screenshot name, status, error text, and the batch id in `<title>`. An attribute value goes through `urllib.parse.quote` first where it is a URL part, then `html.escape`. A case titled `<img src=x onerror=alert(1)>` shows as text.
- **AC-15**: The test cases view has a "Run Selected on Backend" button (`btn-run-selected-backend-batch`) beside "Run Selected Batch", inside the same `testcase-bulk-script-container`. So it shows under the same rule (approved test cases exist), and it carries `data-requires="Executions.create"` like its neighbour. "Run Selected Batch" keeps opening the workspace and keeps its `alert` on errors. On click, with no case selected, it alerts "Please select at least one test case to execute." and stops. Otherwise it calls `setBatchButtonsBusy(true)` (both batch buttons disabled), then:
  1. Calls `generateMissingScripts(ids, onProgress)`, the helper both buttons share. The button text shows `Generating scripts (generated/total)`. When every selected case has a script, the helper does nothing. If it throws, the error shows as an error toast, `setBatchButtonsBusy(false)` runs, and no batch starts.
  2. Calls `API.executeBatch(projectId, ids, this.currentTestCycle?.id ?? null)`. A null cycle leaves out the `test_cycle_id` query param. A start error (`404 Test cycle not found`, a project readiness error, `400 No runnable test cases selected`) shows the server's message as an error toast, then `setBatchButtonsBusy(false)` runs.
  3. On success, it logs `Started backend batch <batch_id> for N test cases.`, where N is the number of selected cases. It sets `window.location.hash` to `#/execution/BATCH-<batch_id>` and calls `setBatchButtonsBusy(false)`. A new batch id always changes the hash, so this needs no "same hash" case.
- **AC-16**: The route `#/execution/BATCH-<batch_id>` opens the live observer for that batch. It strips the `BATCH-` prefix and streams `GET /api/v1/projects/{currentProject.id}/batches/{batch_id}/stream`. Progress done is `total_tasks - remaining_tasks` of `total_tasks`. Passed, failed and skipped come straight from the events' `passed_count`, `failed_count` and `skipped_count`. Log lines from `log` appear as they arrive. Events without these fields (the first `Started`) leave the display as it is. The final `Completed` or `Error` event carries the final counts, so there is no extra fetch. On `Error` the bar stays at its partial value. Reloading the page replays the stream from the batch's first event. When no project is selected (a deep link, a reload as a non admin user with no saved project, and always for an admin, who starts with no project), the observer shows "Select the project that ran this batch to watch it." and opens no stream.
- **AC-17**: When the stream sends `Completed` or `Error`, the observer sets a `finished` flag, shows "Finished", closes the stream, and refreshes project data. Then `source.onerror` (which the browser also fires when the server closes the stream) never replaces "Finished" with "Disconnected". On `Completed`, or on an `Error` without `untracked: true`, it shows one "View batch report" button (`obs-batch-report-btn`, under the progress bar, rendered once). That button opens the report through `openAuthorizedReport(() => API.getBatchReportHtml(this.currentProject.id, batchId))` with the stripped id (AC-8). The stream route calls `batch_log_state(project_id, batch_id)` in the route body, before it builds the response. When that is `None` (unknown id, an id that isn't a UUID, another project's batch, or the server restarted), the stream sends only `{"status": "Error", "untracked": true, "log": "This batch is not tracked on this server. It may belong to another project, or the server restarted."}`, with no "Connecting" event before it, and ends. It never answers 422 or waits forever.

## Decision

**Chosen option**: Option 2: a separate `batch_id` column on executions

Every run gets a nullable UUID `batch_id`, set by whoever starts the batch, and `test_cycle_id` goes back to meaning only a real test cycle.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

**Data model sketch**:

| Entity | Field | Type | Null | Rule |
|---|---|---|---|---|
| `executions` / `ExecutionResult` | `batch_id` (new) | UUID | yes | Index `ix_executions_project_batch` on (`project_id`, `batch_id`). No foreign key. |
| `executions` / `ExecutionResult` | `test_cycle_id` (meaning narrowed) | UUID, FK `test_cycles.id`, `ON DELETE SET NULL` | yes | A real test cycle of this project only |
| `accessibility_reports` / `AccessibilityReport` | `batch_id` (source changes) | String(100) | yes | `str(ExecutionResult.batch_id)`. Column unchanged. |

Relationships:
* One test cycle has many runs.
* One batch id is shared by many runs of one project. A batch has no table of its own.
* A batch's runs share one cycle or none.
* One run has at most one accessibility report.

**API surface**:
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/v1/projects/{id}/batches/execute` | POST | body: list of test case UUIDs (unchanged) · query `test_cycle_id`: UUID (optional, new) | `status`, `batch_id` | `Executions.create` | 404 test case or cycle not found, 422 empty list or bad id |
| `/api/v1/projects/{id}/accessibility/gate` | GET | `execution_id` or `batch_id` (exactly one) | `gate_status`, `gate_reason` (adds `batch_running`, `batch_no_runs`), `gate_mode`, `runs`, `in_progress` (new) | `Executions.view` Bearer token, or `X-Webhook-Secret` | 422 neither or both, 404 (no runs, no reports, and no event log of this project) |
| `/api/v1/projects/{id}/batches/{batch_id}/html` | GET | `batch_id`: UUID | HTML report (`text/html`), fetched by the UI as text | `Executions.view` + project access, Bearer token (new) | 401, 403, 404 |
| `/api/v1/projects/{id}/batches/{batch_id}/stream` | GET | `batch_id` (path) | Server sent events (a one way live stream) of the batch log. Ends after `Completed` or `Error`. An untracked batch gets one `Error` event, then ends (new) | none (unchanged, see Follow-up) | never an HTTP error |
| `/api/v1/projects/{id}/executions`, `/executions/{eid}` | GET | none | each run now carries `batch_id` | unchanged | unchanged |
| Workspace `completed` webhook (`finalize_execution`) | POST | `batch_id`: UUID string (optional, new) · `test_cycle_id` (now checked) | saved run | `X-Webhook-Secret` (unchanged) | never fails on a bad `batch_id` or `test_cycle_id` |
| Workspace `/api/execute` (Next.js) | POST | `batchId` (optional, new) beside `testCycleId` | execution id | unchanged | unchanged |

**Value sourcing**:
| Action | Value | Source |
|---|---|---|
| Backend batch start | `batch_id` | `uuid4()` in `ExecutionService.start_batch_run`, already returned to the caller |
| Backend batch start | `test_cycle_id` | Query param, checked through `get_test_cycle` then `get_release(cycle.release_id).project_id == project_id` |
| UI batch start | cycle id | `this.currentTestCycle.id` in `frontend/js/app.js`, passed by `api.executeBatch(projectId, ids, testCycleId)` |
| Workspace batch | `batchId` | `crypto.randomUUID()` once per `runBatchSequentially` call in `backend/playwrightt/app/page.tsx`, sent to `/api/execute`, stored in queue `metadata.batchId` |
| Workspace webhook | `batch_id` | `execution.metadata.batchId` in `sendWebhook`, next to the existing `test_cycle_id` lookup |
| Webhook save | first saved `batch_id` | `repo.get_execution_result(project_id, exec_id).batch_id` when present (same read as the snapshot reuse) |
| Gate run list | runs of a batch | New `get_execution_results_for_batch(project_id, batch_id)` on the repository interface, using the index on PostgreSQL |
| Gate `in_progress` | batch running | `ExecutionService.batch_log_state(project_id, batch_id)` is `running`. It reads `active_batch_events[batch_id][-1]["status"]` |
| Gate empty batch | log state | `batch_log_state` returns `running`, `ended`, or `None`. It returns `None` when there is no log, or when the log's first event's `project_id` isn't this project. `start_batch_run` creates the log already holding its `Started` event with `project_id` (so it is never empty), and the log stays a plain list for the stream route and the chat assistant |
| Gate `in_progress` (every path) | flag | Set by the route after the gate returns, from `batch_log_state(...) == "running"`. `gate_for_batch` never sets it. When runs or reports exist, the log state only feeds this flag |
| Gate empty batch | `gate_reason` | New constants `GATE_REASON_BATCH_RUNNING = "batch_running"` and `GATE_REASON_BATCH_NO_RUNS = "batch_no_runs"`, next to `GATE_REASON_NO_REPORT` in `accessibility_service.py` |
| UI batch report | Bearer token | `sessionStorage.access_token`, sent by `request(path, {responseType: 'text'})` in `frontend/js/api.js` |
| UI batch report | base URL | `window.location.origin` (the backend serves the app and the API from one origin) |
| UI batch report | error text | `error.message` from the `request` helper, which carries the server's `detail` |
| Accessibility report | `batch_id` | `ExecutionResult.batch_id` in `AccessibilityService.process` |
| Batch HTML report | runs | `get_execution_results_for_batch` |
| UI picker | grouping key | `execution.batch_id` from the executions list response |
| UI backend batch button | selected cases | `this.getSelectedTestCases()`, the same selection "Run Selected Batch" reads |
| UI backend batch button | which cases need a script | Selected cases whose `this.testCases` entry has no `playwright_script` |
| UI backend batch button | generated scripts | `API.generatePlaywrightScript(projectId, caseId, this.currentProject.framework)`, five at a time, inside `generateMissingScripts(ids, onProgress(generated, total))`. It returns the number generated, throws on the first failure, and after generating calls `refreshProjectData()` and `renderTestCases()` |
| UI backend batch button | button labels | `setBatchButtonsBusy(busy)` disables or enables both buttons and restores "Run Selected Batch" and "Run Selected on Backend" |
| UI backend batch button | N in the log line | `ids.length` (the selected cases) |
| Live observer | counts | `passed_count`, `failed_count`, `skipped_count` from each event. Done is `total_tasks - remaining_tasks` |
| Live observer | project | `this.currentProject.id`. With no project selected, the observer shows the "Select the project" message |
| Live observer | report button shown | `status` is `Completed`, or `Error` without `untracked: true` |
| UI backend batch button | error text | `error.message` from the `request` helper (the server's `detail`) |
| Live observer | batch id | The route `#/execution/BATCH-<batch_id>` with the `BATCH-` prefix removed |
| Live observer | progress, counts, logs | The batch stream events (`progress`, `total_tasks`, `remaining_tasks`, `status`, `log`) written by `start_batch_run` |
| Live observer | report | `openAuthorizedReport(() => API.getBatchReportHtml(projectId, batchId))` |
| Stream route | untracked batch | `ExecutionService.batch_log_state(project_id, batch_id) is None` |
| JSON backfill | real cycle ids | Keys of `raw["test_cycles"]` in the JSON store |

**Key invariants**:
- `test_cycle_id` is null or the id of a real cycle in the run's own project. It never holds a batch id.
- All runs sharing a `batch_id` are in one project. Every batch read filters by `project_id` first.
- A saved `batch_id` never changes after the first save.
- No batch read uses `test_cycle_id`.
- A gate answer that comes only from the event log (AC-12, AC-13) needs the log's project id to equal the path's project id.
- No access token or signature ever appears in a URL.

**Security model**: Batch reads stay project scoped. The gate keeps its Bearer token or workspace secret rule from spec 0011. The batch HTML route gains `require_project_permission("Executions", "view")`. Auth is Bearer only, with no cookie, so a plain `window.open` of the route gets a 401. The UI fetches it with the token and opens a blob page. A blob page has the app's origin, and a tab opened by `window.open` gets a copy of the app's `sessionStorage`, which holds the tokens. So the report must never run script: every interpolated value is escaped (AC-14), the report has no `<script>`, and the UI adds a CSP `<meta>` with `default-src 'none'` as a second guard. The batch report route never sends exception text to the client. The empty batch answers check the log's project, so one project can't learn that another project's batch exists. A cycle from another project gets a 404, so its existence isn't revealed (spec 0009 rule). A webhook `batch_id` can't join another project's batch, because every read filters by project. `GET /batches/{batch_id}/stream` stays unguarded (a browser `EventSource` can't send a Bearer token) and is listed in Follow-up. It now streams only a batch whose log belongs to the path's project, so another project's batch log can't be read through it. The new button carries `data-requires="Executions.create"`, and the start route still checks `Executions.create` itself.

**Observability**: Log a warning naming the execution id when a webhook `batch_id` is dropped as invalid, or a `test_cycle_id` is dropped as unknown. The JSON backfill logs how many runs it moved.

**Configuration required**: none.

**Critical test scenarios**:
- Happy path: a backend batch of two cases on the JSON store and on PostgreSQL. Both runs read back with the response's `batch_id`, the gate lists both, and the batch HTML report shows both. Verifies **AC-1**, **AC-6**, **AC-8**
- Cycle: a batch started with a real cycle saves that cycle on both runs. Another project's cycle gives 404 and no run. Verifies **AC-2**
- Workspace: two `completed` webhooks with the same `batch_id` are saved with it. A webhook with `batch_id: "x"` saves null. A repeated webhook with a different `batch_id` keeps the first. Verifies **AC-3**, **AC-5**
- Stray cycle: a webhook with an unknown `test_cycle_id` saves null on the JSON store too. Verifies **AC-5**
- In progress: the gate reports `in_progress: true` before the batch's `Completed` event, and `false` after it. Verifies **AC-7**
- Auth: the batch HTML route without a session gives 401, and for another project's batch gives 404. Verifies **AC-8**
- Backfill: a store with one fake cycle id and one real one. After migration the fake id is in `batch_id`, the real one is untouched, and a second run changes nothing. Verifies **AC-10**
- Empty running batch: with a running log for project A and no saved run, the gate gives 200 `batch_running`, `in_progress: true`. Project B asking for the same id gets 404. Verifies **AC-7**, **AC-12**
- Empty ended batch: a log whose last event is `Error` and no run gives 200 `batch_no_runs`, `in_progress: false`. With the log removed, the same request gives 404. Verifies **AC-13**
- Report open: in the browser, "View HTML Report" for a batch opens the report with its screenshots. With the backend returning 404, the tab closes and a toast shows "No executions found". Verifies **AC-8**
- Escaping: a case titled `<img src=x onerror=alert(1)>` and a step observation `<script>` appear as text in the batch HTML. Verifies **AC-14**
- UI start: select two cases (one without a script) with a cycle selected, click "Run Selected on Backend". The missing script is generated, the request carries `?test_cycle_id=`, the page lands on `#/execution/BATCH-<id>`, and both runs save with that batch and cycle. Verifies **AC-9**, **AC-15**
- UI start errors: no selection alerts; a stale cycle shows the `Test cycle not found` toast and no navigation. Verifies **AC-15**
- Observer: progress and logs update live, a reload replays them, and on `Completed` the "View batch report" button opens the report. Verifies **AC-16**, **AC-17**
- Untracked stream: another project's batch id, or a random id, gets one `Error` event and the stream ends. Verifies **AC-17**

## Build plan

Tracer Bullet: the first slice carries one backend batch through the model, both stores, the migration, and the gate. Later slices add the other producers and readers.

**Slice 1: thin thread, backend batch to gate**
1. Add `batch_id: UUID | None = None` to `ExecutionResult`, and the column plus the `(project_id, batch_id)` index to `ExecutionDB`. Add an Alembic migration whose `down_revision` is the current head (`alembic heads`). Satisfies **AC-1**, **AC-10**
2. Map `batch_id` in `PostgresProjectRepository.save_execution_result` and `_to_execution_result`. Add `get_execution_results_for_batch` to the repository interface, the JSON store, and PostgreSQL. Satisfies **AC-1**, **AC-6**
3. `start_batch_run` sets `batch_id=batch_id` and stops setting `test_cycle_id` to it. Satisfies **AC-1**, **AC-11**
4. `AccessibilityService.process` copies `ExecutionResult.batch_id`. The gate route reads runs through `get_execution_results_for_batch`, and returns 404 for a `batch_id` that isn't a UUID. Satisfies **AC-6**
5. Remove the `test_cycle_id` restore added to `_saved_or` during the earlier debug fix, which this column replaces. Rewrite its two regression tests in `backend/tests/test_execution_service.py` to assert `batch_id`. Satisfies **AC-1**, **AC-6**

**Slice 2: batch start inside a cycle, and the UI**
6. The batch start route takes `test_cycle_id: UUID | None = None` and checks it (cycle, then release, then project) before anything is queued. `start_batch_run` saves it on each run. Satisfies **AC-2**
7. `api.executeBatch` sends `test_cycle_id` when given, and `app.js` passes `this.currentTestCycle?.id`. The picker groups by `batch_id`. Satisfies **AC-9**
8. The batch HTML route gets `require_project_permission("Executions", "view")`, and `compile_combined_batch_report_html` reads through `get_execution_results_for_batch`. Satisfies **AC-8**

**Slice 3: workspace batches**
9. In `page.tsx`, `runBatchSequentially` makes `crypto.randomUUID()` once and sends `batchId` (and `testCycleId` only when set) to `/api/execute`. Remove the random cycle id. `/api/execute` stores `batchId` in queue metadata. Satisfies **AC-3**
10. `sendWebhook` adds `batch_id` from `metadata.batchId`. The engine's context reuse (around `execution-engine.ts` lines 334 and 1203) checks `batchId` instead of `testCycleId`. The status route returns `batchId`. Satisfies **AC-3**, **AC-4**
11. `finalize_execution` parses `batch_id` (null plus a warning when invalid), keeps a saved `batch_id`, and checks `test_cycle_id` against this project's cycles (null plus a warning). Satisfies **AC-5**

**Slice 4: in progress flag**
12. Add `ExecutionService.batch_in_progress(batch_id)`. Add `in_progress: bool` to `AccessibilityGateResponse` and set it in the gate route. Satisfies **AC-7**

**Slice 5: old runs and docs**
13. Add `run_batch_identity_migration_0012` to the repository interface (it does nothing on PostgreSQL), and implement it in the JSON store with a marker like `knowledge_migration_0006`. Call it where the 0006 migration is called at startup. Satisfies **AC-10**
14. Update `docs/api.md` (batch start param, gate `in_progress`, batch HTML auth, webhook `batch_id`). Update spec 0011's AC-9 line, its `batch_id` data model row, its value sourcing row, and build task 11, so they say `ExecutionResult.batch_id`. Satisfies **AC-6**, **AC-7**, **AC-8**, **AC-11**

**Slice 6: report auth and the empty batch gate (added 2026-11-26)**
15. In `start_batch_run`, create the log as `[{"status": "Started", "project_id": str(project_id), "log": "Initializing batch queue runner..."}]` at line 253, and drop the later separate `Started` append. Add `ExecutionService.batch_log_state(project_id, batch_id)`, which returns `"running"` (last status not `Completed` or `Error`), `"ended"`, or `None` (no log, or the first event's `project_id` is another project). Replace `batch_in_progress(batch_id)` with `batch_in_progress(project_id, batch_id)`, which returns `batch_log_state(...) == "running"`, with no one argument shim. Update its callers in the same slice: the gate route, `test_batch_run_identity.py:113`, and the tests that patch `active_batch_events` (`test_execution_service.py:58`, `:155`, `:327`). The stream route and `chat_actions.py` keep reading the plain list. Satisfies **AC-7**, **AC-12**, **AC-13**
16. Add `GATE_REASON_BATCH_RUNNING` and `GATE_REASON_BATCH_NO_RUNS`. `AccessibilityService.gate_for_batch` takes the log state. When the union of runs and reports is empty, it returns the AC-12 body for `running`, the AC-13 body for `ended`, or `None` for no log. The route turns `None` into 404 and sets `in_progress` on every path. The route stays thin, and the DTO comment lists the new reasons. Add a test for a batch with reports but no runs (a normal gate, not empty). Satisfies **AC-6**, **AC-7**, **AC-12**, **AC-13**
17. In `ReportService.compile_combined_batch_report_html`, pass every interpolated value through `html.escape` (attribute URL parts: `urllib.parse.quote`, then `html.escape`). In `get_batch_html_report`, map only the "No executions found" `ValueError` to 404, and log anything else and return 500 without its message. Satisfies **AC-8**, **AC-14**
18. In `frontend/js/api.js`, add `getBatchReportHtml(projectId, batchId)` using `request(..., {responseType: 'text', headers: {Accept: 'text/html'}})`. In `app.js`, add a generic `openAuthorizedReport(fetchHtml)` that runs the AC-8 steps: blank tab or popup blocked toast, "Loading report…", fetch, `<base>` and CSP `<meta>` insert, blob URL, revoke after 60 s, and on failure close the tab and call `showToast(message, 'error')` unless `error.shown`. Wire the batch branch of `btn-dl-report-html` to it. Satisfies **AC-8**
19. Update `docs/api.md`: the gate's `batch_running` and `batch_no_runs` answers (CI checks `in_progress` before `gate_status`, and `batch_no_runs` means the batch ended before any run was saved), and that the batch HTML needs the Bearer token and backend tests for tasks 15 to 17. Satisfies **AC-8**, **AC-12**, **AC-13**, **AC-14**

**Slice 7: start a backend batch from the UI (added 2026-11-26, Update 2)**
20. In `get_batch_stream`, call `ExecutionService().batch_log_state(project_id, batch_id)` in the route body, before building the `StreamingResponse`. For `None`, return a stream that yields only the AC-17 `Error` event (with `untracked: true`, no "Connecting") and ends. Add backend tests: a tracked batch streams to `Completed`, an unknown id and another project's id each get one `Error` event. Satisfies **AC-17**
21. In `frontend/js/app.js`, move the "generate missing scripts, five at a time" step out of the `btn-run-selected-batch` handler into `generateMissingScripts(ids, onProgress)` (contract in Value sourcing), and add `setBatchButtonsBusy(busy)`. The workspace handler uses both. It writes its tab messages from `onProgress(generated, total)` and keeps its `alert` on a thrown error, so its behavior doesn't change apart from also disabling the new button. Satisfies **AC-15**
22. Add `btn-run-selected-backend-batch` to `frontend/index.html` inside `testcase-bulk-script-container`, beside `btn-run-selected-batch`, with `data-requires="Executions.create"`. Wire its handler in the same `app.js` block that wires `btn-run-selected-batch`, following the AC-15 steps: the helper, `API.executeBatch`, `showToast(message, 'error')` for errors, and the hash change on success. Satisfies **AC-9**, **AC-15**
23. In `renderRealTimeObserver`, strip the `BATCH-` prefix for the stream URL. Show the "Select the project" message when there's no current project. Add the `finished` flag so `onerror` doesn't override "Finished". Render `obs-batch-report-btn` once under the progress bar on `Completed`, or on an `Error` without `untracked`, wired to `openAuthorizedReport`. Bump `app.js?v=39` to `v=40` in `index.html`. Satisfies **AC-16**, **AC-17**
24. Update `docs/api.md` (the stream's untracked batch event) and the executions or testing guide in `docs/` that describes running batches. Satisfies **AC-15**, **AC-17**

## Consequences

**Positive**:
- The batch gate and the batch report work on PostgreSQL, which CI needs.
- Backend batches can count toward a real cycle, and the cycle view shows them.
- Workspace batches outside a cycle reuse the browser context, like backend batches, so login state carries across cases.
- JSON and PostgreSQL agree on what `test_cycle_id` can hold.

**Negative / tradeoffs**:
- Batch runs saved on PostgreSQL before this change stay without a batch. Their id was never saved, so nothing can recover them.
- The JSON backfill treats any `test_cycle_id` with no matching cycle as a batch id. A run whose real cycle was deleted from the JSON store becomes a one run "batch".
- `in_progress` comes from process memory. After a backend restart, a batch that was running reports `false`, and the gate shows what was saved. Workspace batches always report `false`. A workspace batch with no saved run yet still gives 404, so CI polling a workspace batch must treat an early 404 as "maybe not yet".
- The batch report opens at a `blob:` address. Reloading or bookmarking it doesn't work, and the user goes back to the app to open it again.
- The blob page shares the app's origin and its copy of the tokens. Escaping and the CSP `<meta>` keep report content from reading them, so any new field added to the report must be escaped too, and the report can never add its own script.
- The event log is never cleaned up, the same as before this change, so it grows with every backend batch until a restart.
- The test cases view has two batch buttons. You have to know the difference: the workspace run shows the browser live, and the backend run gives a CI readable batch and the live observer.
- The live observer reads process memory, so after a backend restart a running batch's observer shows the "not tracked" error, even though its saved runs are still in the executions list.
- If the batch worker dies without writing `Completed` or `Error` (a killed thread, or an exception the batch's `except Exception` doesn't catch), the stream and the observer wait forever. You could add a stale timeout later if this shows up in practice.
- Admins open the app with no project selected, so a shared observer link shows "Select the project" until they pick the project.
- The backend button generates missing scripts in the browser tab that clicked it. Closing that tab partway stops the batch from starting, and the scripts already generated stay saved.
- A batch has no record of its own (who started it, when, how many cases were planned). Listing batches means grouping runs.
- An older workspace build that still sends a random cycle id gets its runs saved with no cycle and no batch until it's updated. The warning log shows this.

**Neutral**:
- One schema migration (a nullable column and an index), so no table rewrite or downtime.
- The webhook contract gains an optional field, and old payloads stay valid.

## Migration plan

**Strategy**: Add the nullable column, deploy code that writes it, then backfill JSON once.
**Phases**:
1. The Alembic migration adds `executions.batch_id` (nullable) and its index. It's safe while old code runs, because old code ignores the column.
2. Deploy slices 1 to 4, so new runs write `batch_id`.
3. The JSON store migration runs once at startup, guarded by its marker.
**Rollback**: Revert the code, and run the Alembic downgrade to drop the index and column. The JSON migration isn't reversed automatically. Reverted code then treats moved runs as having no batch, and their ids stay readable in `batch_id`.
**Risks**: The JSON migration rewrites the store file, so you may want to copy `project_store.json` first. The cycle check on the batch start route could reject a cycle that the UI shows from a stale list. That's a correct 404, and the UI should refresh its cycles.

## Follow-up

- [ ] `GET /api/v1/projects/{id}/batches/{batch_id}/stream` has no permission check (spec 0009 territory). A browser `EventSource` can't send the Bearer token, so guarding it needs a short lived stream ticket or a fetch based stream reader in the observer. Slice 7 limits it to the path project's own batch logs meanwhile.
- [ ] `GET /api/v1/projects/{id}/executions` and `/executions/{eid}` show no permission dependency in their signatures. You could confirm they're covered, under spec 0009.
- [ ] If CI later needs "the batch is finished" for workspace batches too, a batches table (Option 3 in rationale.md) would hold the planned case count.
- [ ] When spec 0009 guards the per run HTML, PDF and JUnit routes, you could switch their buttons to `openAuthorizedReport` (PDF and JUnit as a download). Their media routes (`/api/artifacts/...`, `.../video`) are unguarded today too. If 0009 guards those, the blob page's `<img>` and `<video>` tags can't send a token, so the batch report would need its media inlined or fetched as blobs.
- [ ] The per run HTML report (`professional_report.html`) is served from the app origin too. You could check it escapes run values the same way as AC-14.
