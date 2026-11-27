# Verify: batch run identity · spec 0012 · updated 2026-11-26 (report auth, empty batch gate)
_Steps derived from spec 0012 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

_Not built yet, so not verifiable: the spec 0011 text updates (part of task 14). The UI backend batch start is specified in AC-15 to AC-17 (slice 7) and verifiable once built._

## UI / manual
- [ ] Start a backend batch of two cases on the JSON store (`POST /api/v1/projects/{id}/batches/execute`) → both runs read back from `GET /executions` with the response's `batch_id`, and `test_cycle_id` is null → AC-1, AC-11
- [x] Repeat on PostgreSQL (`REPOSITORY_PROVIDER=postgres`) → same result, and `SELECT batch_id FROM executions WHERE id = ...` shows the id → AC-1
- [x] Start a batch with `?test_cycle_id=<real cycle of this project>` → both runs have that cycle and the batch id, and the test cycle view shows them → AC-2, AC-9
- [x] Start a batch with another project's cycle, then with a random UUID → `404 Test cycle not found`, and no runs appear → AC-2
- [ ] In the workspace, run two selected cases with no cycle → both `completed` webhooks carry the same `batch_id`, no `test_cycle_id`, and the second case reuses the browser context (log line "Batch ... active: keeping context open") → AC-3, AC-4
- [ ] Run a single case in the workspace → no `batch_id` in the webhook, and the context closes after it → AC-3, AC-4
- [x] Open the workspace from the app with no cycle selected after an earlier run with a cycle → the run sends no `test_cycle_id` (the stale localStorage cycle is cleared) → AC-3
- [ ] Send a `completed` webhook with `batch_id: "x"` → the run saves `batch_id: null`, and a warning names the execution id → AC-5
- [x] Repeat a webhook for the same `execution_id` with a different `batch_id` → the first one is kept → AC-5
- [ ] Send a webhook with an unknown `test_cycle_id` on the JSON store → saved as null with a warning → AC-5
- [x] `GET /accessibility/gate?batch_id=<backend batch on PostgreSQL>` → 200, lists both runs → AC-6
- [x] `GET /accessibility/gate?batch_id=abc`, and with another project's batch id → 404 → AC-6
- [x] Poll the gate while a backend batch runs → `in_progress: true`, then `false` after `Completed`. A workspace batch, and the `execution_id` gate, always report `false` → AC-7
- [x] Poll the gate right after starting a backend batch, before the first case is saved → 200, `gate_reason: batch_running`, `runs: []`, `in_progress: true`. The same batch id under another project → 404 → AC-7, AC-12
- [ ] Start a batch whose only case errors before any run is saved → 200, `gate_reason: batch_no_runs`, `in_progress: false`. Restart the backend, then ask again → 404 → AC-13
- [x] Open the batch HTML report from the executions picker while signed in → a new tab shows the batch's runs with screenshots and video. `curl` without a Bearer token → 401. Another project's batch → 404 → AC-8
- [x] Make the batch report fail (a batch id with no runs) → the blank tab closes and an error toast shows the message. Block popups and click again → the "Allow pop ups" toast, and no request is sent → AC-8
- [x] Open the batch report and check the page source → a CSP `<meta>` with `default-src 'none'` follows `<head>` → AC-8, AC-14
- [x] Rename a case to `<img src=x onerror=alert(1)>`, run it in a batch, open the batch report → the title shows as text, no alert → AC-14
- [x] The executions picker shows one entry per `batch_id`, and a run with a cycle but no batch is listed on its own → AC-9
- [ ] Chat `run_batch` → its runs have a `batch_id` and no cycle → AC-11

## Value sourcing
- [x] Backend `batch_id` is the `uuid4()` from `start_batch_run`: the response id equals every saved run's id → AC-1
- [x] Backend `test_cycle_id` comes from the query param, checked through cycle, release, project: a cycle whose release is in another project is refused → AC-2
- [x] UI cycle id comes from `this.currentTestCycle.id`: select a cycle, then start → the request carries `?test_cycle_id=` (after slice 7) → AC-9, AC-15
- [x] Workspace `batchId` is made once per `runBatchSequentially` call: two separate batch runs get two different ids → AC-3
- [x] Webhook `batch_id` comes from queue `metadata.batchId`: check `/api/status?executionId=` shows `batchId` → AC-3
- [ ] Webhook save keeps the first saved `batch_id` (both stores) → AC-5
- [x] Gate run list comes from `get_execution_results_for_batch`: a run with the id in `test_cycle_id` only is not listed → AC-6
- [ ] `in_progress` reads `active_batch_events[batch_id][-1]["status"]` through `batch_log_state(project_id, batch_id)`: restart the backend mid batch → `false` → AC-7
- [x] The empty batch answer checks the log's first event `project_id`: another project's id gives 404, not `batch_running` → AC-12, AC-13
- [x] The UI report request carries `Authorization: Bearer` (browser network tab), and the opened page is a `blob:` address with a `<base href>` of the app origin → AC-8
- [x] Accessibility report `batch_id` is `str(ExecutionResult.batch_id)`, not the cycle → AC-6
- [x] Batch HTML report runs come from `get_execution_results_for_batch` → AC-8
- [x] UI picker grouping key is `execution.batch_id` → AC-9
- [x] JSON backfill treats keys of `raw["test_cycles"]` as real cycles: a run with a deleted cycle becomes a one run batch → AC-10

## Commands
- [ ] `alembic upgrade head`, then inspect `executions` → `batch_id` column and `ix_executions_project_batch` index exist; `alembic downgrade -1` removes both → AC-10
- [ ] Copy `project_store.json`, start the backend once → the log says "Batch identity migration 0012 moved N run(s)", fake cycle ids are now in `batch_id`, real ones untouched; a second start changes nothing → AC-10
- [x] `pytest -q backend/tests/test_batch_run_identity.py backend/tests/test_execution_service.py backend/tests/test_accessibility.py` → all pass → AC-1 to AC-10
- [x] `pnpm --dir backend/playwrightt build` → succeeds → AC-3, AC-4

## Acceptance-criteria coverage
- AC-1: backend batch steps, value sourcing 1 · AC-2: cycle start steps · AC-3: workspace steps · AC-4: context reuse steps · AC-5: webhook steps · AC-6: gate steps · AC-7: in progress step · AC-8: HTML report step · AC-9: picker step and UI start step · AC-10: migration commands · AC-11: response and chat steps · AC-12: empty running gate step · AC-13: empty ended gate step · AC-14: escaping step

## Added by /develop, slice 6 build (2026-11-26)
- [x] Click "View HTML Report" for a batch on a slow backend → the new tab first shows "Loading report…", then the report → AC-8
- [ ] Make `compile_combined_batch_report_html` raise something other than "No executions found" (for example a broken store file) → `500` with `Batch report could not be built`, no exception text in the body, and a logged traceback naming the batch id → AC-8
- [x] Open a batch report, wait over 60 s, then reload the tab → the reload fails (the blob URL was revoked), and reopening from the app works → AC-8
- [x] After a hard refresh, the browser loads `api.js?v=29` and `app.js?v=39` (network tab), so the new report opener is live → AC-8
- [x] `pytest -q backend/tests/test_batch_run_identity.py -k "log_state or empty or in_progress_once or escapes or hides"` → all pass → AC-7, AC-8, AC-12, AC-13, AC-14

## Added by /architect, slice 7 spec (2026-11-26)
- [x] With a cycle selected, select two cases (one with no script) and click "Run Selected on Backend" → the button shows `Generating scripts (1/1)`, then the request carries `?test_cycle_id=`, the page lands on `#/execution/BATCH-<id>`, and both runs save with that batch and cycle → AC-9, AC-15
- [x] Click "Run Selected on Backend" with nothing selected → the "Please select at least one test case" alert, no request → AC-15
- [x] Delete the selected cycle in another tab, then start → the `Test cycle not found` toast, no navigation, both buttons enabled again → AC-15
- [x] Sign in as a user without `Executions.create` → the button is hidden → AC-15
- [x] "Run Selected Batch" still opens the workspace as before → AC-15
- [x] On the observer, progress and log lines update while the batch runs; reload mid batch → the log replays from the first event → AC-16
- [x] When the batch ends → "Finished", and "View batch report" opens the report as a blob page → AC-17
- [x] `curl -N /api/v1/projects/<other project>/batches/<batch id>/stream` and a random id → one `Error` event, then the stream ends → AC-17
- [x] Open `#/execution/BATCH-<id>` as an admin right after sign in (no project selected) → "Select the project that ran this batch to watch it.", no stream request → AC-16
- [x] After the batch ends, wait a few seconds → the badge still says "Finished", not "Disconnected" → AC-17
- [x] Open `#/execution/BATCH-<random uuid>` with a project selected → one untracked `Error` line, "Finished", and no "View batch report" button → AC-17
- [x] On the test cases view with no approved cases → neither batch button shows → AC-15
- [x] `pytest -q backend/tests/test_batch_run_identity.py -k stream` → all pass → AC-17
