# 0012. Give batch runs their own batch id: rationale

## Context

`ExecutionResult.test_cycle_id` carries two meanings. A real test cycle is a row in `test_cycles` under a release of the project. The UI filters a cycle's runs by it, and workspace runs started from a cycle send it in their webhook. A batch is a set of runs started together. `ExecutionService.start_batch_run` puts a fresh `uuid4()` into the same field, and the workspace page invents a random "cycle" id for a batch when no cycle is selected (`runBatchSequentially` in `backend/playwrightt/app/page.tsx`).

The stores disagree about the second meaning. `executions.test_cycle_id` in PostgreSQL is a foreign key to `test_cycles`, and `PostgresProjectRepository.save_execution_result` saves an unknown id as null. The JSON store keeps any id. So on PostgreSQL every batch loses its identity at save time. Spec 0011's batch gate (AC-9), which was specified as "the batch id is the runs' `test_cycle_id`", returns 404 for a batch that ran. `compile_combined_batch_report_html` reports "No executions found". A `/debug` session on 2026-11-26 patched `_saved_or` to put the id back on the in memory copy, which saved the accessibility report's `batch_id` but not the run's.

The overlap also hurts on the JSON store. A backend batch can never belong to a real cycle, because its cycle field is taken. The executions UI groups by `test_cycle_id` to build its batch picker, so every workspace run in a cycle shows up as one large "batch". The workspace engine decides whether to keep the browser open between cases by checking `testCycleId`, so a workspace batch outside a cycle closes the browser after every case.

Constraints: the JSON store is the default and PostgreSQL is optional, so both must behave the same (root `AGENTS.md`). Routes stay thin. Migrations go through Alembic for PostgreSQL, and through a marker guarded run once migration for JSON (the `knowledge_migration_0006` pattern). The workspace is in the same repo and ships with the backend.

## Options considered

### Option 1: Fix in place, make `test_cycle_id` accept batch ids

Drop the foreign key on `executions.test_cycle_id` (or stop the save guard from nulling unknown ids), so PostgreSQL keeps batch ids the way JSON does.

**Pros**:
- The smallest code change, a single migration, and no new field anywhere.
- Spec 0011 and the gate route stay as written.

**Cons**:
- It cements the double meaning. A batch still can't belong to a real cycle, and the cycle view still can't tell a batch from a cycle.
- It loses referential integrity on a real relationship (deleting a cycle no longer nulls its runs).
- The UI picker and the engine's context reuse stay wrong.

### Option 2: A separate `batch_id` column on executions

Add a nullable UUID `batch_id` to every run. Batch producers set it, batch readers filter by it, and `test_cycle_id` means only a real cycle.

**Pros**:
- Each field has one meaning, so a run can be in a batch, a cycle, both, or neither.
- Works the same on both stores with one nullable column, and an index keeps batch reads cheap.
- No new table, so no row to create first and no lifecycle to manage.

**Cons**:
- Touches every producer and reader of batch identity (backend, workspace page and engine, webhook, UI, report, gate).
- Old PostgreSQL batch runs can't be recovered, and the JSON backfill has to guess which ids were batches.
- No place for batch level facts like the planned case count or who started it.

### Option 3: A `batches` table with `executions.batch_id` as a foreign key

A row per batch (`id`, `project_id`, `source`, `test_cycle_id`, `started_at`, `started_by`, planned count), referenced by each run.

**Pros**:
- Real batch metadata. Listing batches is a query, and "finished" can be computed from the planned count, which would cover workspace batches too.
- Referential integrity for batch membership.

**Cons**:
- Both producers must create the row before the first run is saved. The workspace has no backend start step, so that adds a new endpoint and a new failure mode (row creation fails, so runs can't be saved).
- More schema, repository methods, and migration work, for metadata nobody has asked for yet.

## Rationale

The failure is a data modelling one: one field, two meanings, and two stores that enforce different rules on it. Option 1 makes the stores agree by weakening PostgreSQL, but it keeps the overlap that also breaks the UI picker, the cycle view, and the engine's context reuse. It trades a real foreign key for a convenience, which is a cost every later reader of the data pays.

Option 2 separates the meanings at the smallest cost that fixes every symptom in Context. A nullable column is the boring, safe migration for a live system (add nullable, write it, then backfill), and an index on (`project_id`, `batch_id`) keeps it fast on PostgreSQL. It matches how the code already treats a batch: a set of runs sharing an id, with no lifecycle of its own.

Option 3 would be the right call if the product needed batch level facts. The only such need that came up is CI asking "is the batch finished", and the backend batch's event log answers that for backend batches (AC-7). The workspace case stays a known gap, recorded in Follow-up, and Option 3 is the path if it becomes real. Building the table now would add a required write before every workspace batch, a new way for runs to fail to save, for a need that isn't here yet.

Small calls made while writing:
* **Batch lookup.** A repository method `get_execution_results_for_batch`, rather than filtering the full run list in the route and the report. It keeps routes thin and uses the index. The runner up, filtering the list in Python, is simpler but loads every run of the project per gate call.
* **The `_saved_or` stopgap.** Remove it. Once cycle ids are checked before saving, the saved copy matches the in memory result, so the restore has nothing left to do. Keeping it would hide a future save bug. The runner up, keeping it as a guard, costs nothing, but it masks drift between stores.
* **Batch scope.** A workspace batch id is scoped to one call of `runBatchSequentially` and isn't persisted in the browser. A reload starts a new batch for any cases that remain. The runner up, storing it in `localStorage`, would join runs across sessions.

## Update 2026-11-26: report auth and the empty batch gate

### Context

Two gaps came up while building. First, auth is Bearer only (`HTTPBearer` in `backend/dependencies/auth_dependencies.py`), and the UI keeps the token in `sessionStorage`. No cookie exists, so the original plan (open the guarded batch report with `window.open`, "which sends the session cookie") gets a 401. Second, a backend batch saves each run only after that case finishes and its failure is triaged. Until then, the gate finds no runs and returns 404, so CI polling right after the start can't tell "not saved yet" from "wrong id". The in memory event log that answers `in_progress` is keyed only by batch id, with no project.

### Options considered: how the UI authenticates the batch report

**A. Fetch with the Bearer token, open as a blob page.** The UI opens a blank tab on click, fetches the HTML through the shared `request` helper, and points the tab at a blob URL.
* Pros: no new auth path, nothing secret in a URL, reuses the token refresh and the blob pattern the screenshot helper already uses.
* Cons: the page lives at `blob:`, so it can't be reloaded or bookmarked. Root relative media URLs need a `<base>` tag. The page shares the app's origin and its copy of `sessionStorage`, so the report must escape everything.

**B. Short lived signed link.** A new endpoint returns a URL with a signature valid about 60 s, and the report route accepts the Bearer token or the signature.
* Pros: real navigation, so reload (within the window), Save as, and a `Content-Security-Policy: sandbox` header all work. A sandboxed page can't read the app's storage.
* Cons: a second auth path to build, test, and keep correct, with signing keys, expiry, and replay rules. The signature lands in browser history and access logs.

**C. Fetch with the Bearer token, download as a file.**
* Pros: no same origin page at all.
* Cons: the user has to open a downloaded file to see a report they asked to view.

**Chosen: A.** It fixes the 401 with code the app already runs, and adds no new way to authenticate. Its real risk, a same origin page that can read tokens, is closed by escaping (AC-14), which the report needed anyway, since case titles and page observations were going in raw. A CSP `<meta>` with `default-src 'none'` is a second guard, so one missed escape can't run script. B is the runner up. It becomes the better call if reports ever need to run their own scripts, or be shared as links, because only a real response can carry a sandbox header.

### Options considered: the gate for a running batch with no saved runs

**A. `200`, `NOT_SCANNED`, `gate_reason: batch_running`, `in_progress: true`.**
* Pros: a 404 always means a wrong or unknown id, and CI branches on one field it already reads.
* Cons: the answer comes from process memory, so the log needs a project tag to keep the 0009 rule.

**B. `202 Accepted` with the same body.**
* Pros: CI can branch on the status code.
* Cons: unusual for a GET, and one response model covers two meanings.

**C. Keep the 404 and have CI retry.**
* Pros: no change.
* Cons: CI retries a typo until it times out.

**Chosen: A**, plus `batch_no_runs` with `in_progress: false` for a batch whose log ended with nothing saved, so CI fails fast with a reason. Tagging the log with its project (a `project_id` on its first `Started` event) is what keeps another project's batch hidden.

Small calls made while writing:
* **Where `<base>` comes from.** The UI inserts it from `window.location.origin`, since the backend serves the app and the API from one origin. The runner up, the backend writing it from `request.base_url`, can be wrong behind a proxy.
* **Blob URL lifetime.** Revoked after 60 s, because the opener can't reliably see the new tab's load event once its address changes. The runner up, never revoking, leaks the HTML until the app tab closes.
* **Where the empty batch decision lives.** In `AccessibilityService.gate_for_batch` (it takes the log state), so the route stays thin. The runner up, branching in the route, is shorter but puts gate rules in `main.py`.
* **Where the project tag lives** (changed after the cross check). On the log's first event, which `start_batch_run` now creates together with the log. The runner up, a separate `active_batch_projects` dict, can drift from the log. Wrapping the log in a record breaks the stream route and the chat assistant, which read it as a plain list. Creating the log with its first event also means it is never empty, so a brand new batch never reads as `ended`.
* **Popup blocked.** Toast and stop, rather than falling back to a download, because the user asked to view the report.

## Update 2 (2026-11-26): starting a backend batch from the UI

### Context

`/check verify` found that AC-9's "the UI starts a backend batch in the selected cycle" had no control to build on. `api.executeBatch` existed since the first commit, but nothing called it. The test cases view's "Run Selected Batch" generates missing scripts, then opens the Next.js workspace, which runs the cases one by one in a visible browser. The backend batch (`POST /batches/execute`) quietly skips selected cases with no script. The app already has a live observer route, `#/execution/BATCH-<id>`, that reads the batch stream's progress events, but nothing links to it, and it passed the `BATCH-` prefix into the stream URL, so it could never connect. The stream route waits forever for a batch it doesn't know.

### Options considered: where the button goes

* **A second button beside "Run Selected Batch" (chosen).** Reuses the existing case selection and keeps the workspace run as it is. Con: two similar buttons, so testers must learn the difference.
* **Replace the workspace run.** One button, one path. Con: removes the visible browser run from this view, which testers use to watch and debug a run.
* **A start control on the executions view.** Sits where results appear. Con: needs a second case selection UI, so more to build and keep in step with the test cases view.

### Options considered: what you see after starting

* **The existing live observer (chosen).** It already renders batch progress, counts, and logs, and it survives a reload because the stream replays the log. Con: it reads process memory, so it can't follow a batch across a backend restart.
* **A toast, then a list refresh.** Least to build. Con: no progress at all for a run that takes minutes.

### Options considered: selected cases without a script

* **Generate first (chosen).** The same step the workspace button already runs, so behavior matches across both buttons. Con: generation calls the LLM and can take minutes before the batch starts.
* **Refuse with a list.** Predictable, nothing hidden. Con: an extra round trip for the tester.
* **Start and warn.** Fastest start. Con: easy to miss that cases were skipped, which is today's silent skip with a message.

### Rationale

The second button keeps what works (the workspace run) and adds the path CI cares about, since only backend batches get a gate with `in_progress`. Reusing the observer and the script generation step means the new work is mostly wiring, with two small fixes (the prefix and the stream's wait forever) that the observer needs anyway. The stream's project check also closes a read across projects without waiting for the stream guard in Follow-up.

Small calls made here:
* **Script generation helper.** One helper with a progress callback, so both buttons share it. The runner up, copying the loop, would let the two drift.
* **Navigation.** Setting `window.location.hash`, so the existing router opens the observer and a reload lands on the same page. The runner up, calling the render method directly, loses the address on reload.
