# Review, feature/accessibility-agent, 2026-11-27

**Reviewed by**: Claude Sonnet 5.5 (author model not stated)
**Scope**: about 87 files, branch vs main (merge base a903eba), plus untracked new files
**Verdict**: Changes requested

## Summary
This change adds the Accessibility Agent (spec 0011) and batch run identity (spec 0012). It scans pages with axe, explains findings with one LLM call, drafts bugs, gates releases, and gives every batch its own `batch_id`. The code is careful and well covered by tests. Secrets are redacted, paths are checked, the report HTML is escaped, and the batch report is opened safely with a token. One major problem stands out. The workspace webhook runs the slow LLM step before it answers, so every workspace run can be held up for minutes. The rest are minor items.

## Major

### 🟠 Workspace webhook waits for the accessibility LLM call, `backend/services/execution_service.py:677`
**Problem**: `finalize_execution` calls `apply_accessibility` inline. That calls `process`, which runs `_enrich`, which makes the LLM call with a 60 second timeout and one retry. Failure triage right above it is deliberately moved to `background_tasks` so the response is not held up. The accessibility step does not get the same treatment.
**Why it matters**: The Node engine awaits the webhook with `fetch` and no timeout (`execution-engine.ts`, `sendWebhook`). A slow or hung LLM can hold each run open for up to about two minutes. A workspace batch of ten tests could lose twenty minutes. It also ties up a backend worker thread for the whole time. The graph path and the batch path already run in background threads, so only the workspace path has this problem.
**Suggested fix**: Save the report first with the axe fallback text, then run enrichment after the response (for example through `background_tasks`, as triage does) and update the stored report when it finishes. Or at least cap the inline call to a few seconds on this path.

## Minor

### 🟡 Standalone scan ignores the global off switch, `backend/services/accessibility_scan_service.py:269`
**Problem**: `start` never checks `accessibility.enabled`, and `run` passes `enabled=True` to `process`. When an admin turns scanning off with `A11Y_ENABLED=false`, test runs stop scanning but standalone scans still start.
**Why it matters**: The global switch is meant to be a kill switch. It will not stop browser work or LLM spend from this path.
**Suggested fix**: Refuse the scan (409) when the global switch is off, and say so in the dialog.

### 🟡 A failed scan still pays for the LLM call, `backend/services/accessibility_service.py:282`
**Problem**: `process` runs `_fill` and `_enrich` first, and only then checks `raw.get("status") == "failed"`. The LLM is called for findings that are then thrown away behind a `NOT_SCANNED` gate. The failed report also keeps its findings.
**Why it matters**: Wasted tokens, and a report that says failed but still lists findings.
**Suggested fix**: Check the failed status before `_enrich`, and decide on purpose whether partial findings are kept.

### 🟡 Element screenshots collide across runs and are never cleaned, `backend/playwrightt/lib/a11y-scan-session.ts:231`
**Problem**: Screenshots go to `artifacts/<test_case_id>/a11y-shots/<rule>-<hash>.png`. The name depends only on rule, path, and selector, and the folder is shared by every run of that test case. The runner deletes `a11y.json` before a run but not this folder.
**Why it matters**: An old report can show a newer run's picture. The folder also grows without limit.
**Suggested fix**: Clear `a11y-shots` at run start (as `a11y.json` is cleared), or put the run or report id in the folder or file name.

### 🟡 Gate route holds business logic, and a list route reaches into a service, `backend/main.py:3815`
**Problem**: `get_accessibility_gate` parses the batch id, loads runs, reads the log state, and sets `in_progress`. `list_accessibility_reports` (line 3884) calls `service.repo.list_reports` directly. AGENTS.md says routes in `main.py` stay thin.
**Why it matters**: It is harder to test and it will drift. The batch gate rules now live in two places.
**Suggested fix**: Move the batch lookup into a service method such as `gate_for_batch_request`, and add a `list_reports` method on the service.

### 🟡 JSON store parses the whole file on every call, `backend/repository/accessibility_repository.py:162`
**Problem**: Each `get`, `get_by_execution`, `list_by_batch`, and `list_active_scans` loads and validates every report. Reports hold findings and snippets, and they are never pruned. A decode error returns `{}`, so the next save would replace the whole store.
**Why it matters**: Execution detail views, gate calls, and every report list call (which also runs `expire_stale`) get slower as history grows. A damaged file turns into silent data loss on the next write.
**Suggested fix**: Do not return `{}` on a decode error (log and raise, or move the bad file aside). Consider an index by execution id, or treat this as one more reason to move to Postgres.

### 🟡 `crypto.randomUUID()` needs a secure context, `backend/playwrightt/app/page.tsx:386`
**Problem**: The old code built the id with `Math.random`. The new code calls `crypto.randomUUID()`, which is missing when the workspace is opened over plain http on a non localhost address.
**Why it matters**: A workspace batch run would throw on a LAN address that worked before.
**Suggested fix**: Fall back to a small generator when `crypto.randomUUID` is not a function.

### 🟡 Bugs are drafted even when the report did not save, `backend/services/accessibility_service.py:455`
**Problem**: `_upsert` logs and swallows a save failure and returns the unsaved report. `draft_from_accessibility` then drafts bugs that point at an `accessibility_report_id` that does not exist.
**Why it matters**: Dangling links, and a bug that shows a report which cannot be opened.
**Suggested fix**: Have `process` mark the returned report as not saved (or return None), and skip bug drafting in that case.

### 🟡 Bug threshold and gate threshold can disagree, `backend/services/bug_report_service.py:257`
**Problem**: The gate uses the severity saved in the run snapshot. Bug drafting reads the project's current `a11y_block_severity`. Spec AC-6 allows this, but AC-8 promises a stable gate.
**Why it matters**: If someone changes the setting after a run starts, the gate says one thing and the bugs say another.
**Suggested fix**: Read the severity from the report's snapshot for drafting too, or document the difference in `docs/features/accessibility-agent.md`.

### 🟡 Snapshot stores `a11y_enabled` as the string "true", `backend/services/project_configuration.py:229`
**Problem**: The snapshot type is `dict[str, str]`, so a boolean is written as "true" or "false". Only `_truthy` in the accessibility service reads it correctly.
**Why it matters**: Any future code that writes `if snapshot["a11y_enabled"]:` gets True for "false". The wart is contained today, and I judge it acceptable for now because the snapshot type already forces strings.
**Suggested fix**: Add one small helper (for example `snapshot_flag(snapshot, key, default)`), use it in all readers, and note it beside `capture_project_snapshot`.

### 🟡 Scan request has no size limit, `backend/schemas/accessibility_dto.py:28`
**Problem**: `paths: list[str]` has no maximum length. `resolve_paths` loops over the whole list before it checks `max_pages`.
**Why it matters**: A signed in editor can send a very large list. It is a small cost but easy to close.
**Suggested fix**: Use `Field(max_length=...)` on the list and on each path.

### 🟡 `help_url` is stored and linked without a scheme check, `backend/services/accessibility_service.py:355`
**Problem**: The value comes from the scan payload (the workspace webhook sends it). The UI puts it in an `href` after HTML escaping only.
**Why it matters**: A `javascript:` value would survive escaping. The webhook needs the workspace secret, so the risk is low, but the fix is one line.
**Suggested fix**: Keep the value only when it starts with `http://` or `https://`.

### 🟡 Spec 0011 still says the batch id is `test_cycle_id`, `docs/specs/0011-accessibility-agent/index.md:46`
**Problem**: AC-9, the `batch_id` data model row, the value sourcing row, and build task 11 still describe `test_cycle_id`. Spec 0012 build task 14 says to update them, and it is not done.
**Why it matters**: The spec contradicts the code that ships.
**Suggested fix**: Update those four places to `ExecutionResult.batch_id`.

### 🟡 Unrelated edits in `requirements.txt`, `requirements.txt:1`
**Problem**: A byte order mark was added in front of `fastapi`. A machine specific pin, `ormsgpack==1.12.2` (a Windows Smart App Control workaround), was added to the shared file.
**Why it matters**: The pin applies to every developer and to CI. The BOM is noise in the diff.
**Suggested fix**: Remove the BOM. Move the pin out of this change, or keep it only if everyone needs it.

### 🟡 No tests for the TypeScript scan code or the new frontend file
**Problem**: `a11y-scan-session.ts`, `a11y-standalone-scan.ts`, the engine changes, and `frontend/js/accessibility.js` have no automated tests. Only one Playwright spec (`batch-observer-deep-link.spec.ts`) was added.
**Why it matters**: Page cap and budget logic, payload capping, login detection, and the dialog focus handling are all branching logic that only the Python side checks through fakes.
**Suggested fix**: Add small unit tests for `capA11yPayload`, `scanPages` budget and cap handling, and `genericLogin` success checks, plus one e2e for the scan dialog.

## Nits
- ⚪ `backend/main.py:540`, `@app.get("/api/v1/projects",response_model=...)` lost its space after the comma (whitespace only change).
- ⚪ `backend/playwrightt/app/api/status/route.ts:68`, `batchId:` is indented differently from its neighbors.
- ⚪ `backend/services/bug_report_service.py:43`, imports the private `_at_or_above` from another service. Make it public.
- ⚪ `backend/playwrightt/lib/execution-engine.ts:154`, `testCaseId` goes into a path without a UUID check. It is a local trusted route, but one check is cheap.
- ⚪ `frontend/js/accessibility.js:153`, the screenshot object URL is never revoked.
- ⚪ `backend/services/accessibility_scan_service.py:175`, the comment says the budget "can be spent twice", but `scanPages` carries `elapsed_ms` into the second pass, so the budget is shared. Fix the comment or the behavior.
- ⚪ `backend/repository/postgres_accessibility_repository.py:124`, any `IntegrityError` becomes `ActiveScanExists`. A missing project would be reported as a 409.
- ⚪ `backend/models/project.py:38`, the JSON store will refuse to load a project that has a hand edited bad `a11y_gate_mode`. The Postgres mapper is forgiving, so the two stores differ.

## Strengths
- Redaction is shared and applied before text reaches the LLM, the store, Jira, and reports. Vault and test data values are covered, `value` attributes are dropped, and snippets are capped.
- Idempotency is handled with care. Retried webhooks keep one report, the first snapshot, and the first batch id. The gate is computed once from the stored snapshot.
- Good defense in depth around the batch report. Auth is required, every value is escaped, the route never leaks exception text, and the UI adds a CSP and opens the report as a blob with the token.
- Standalone scans are well fenced. They stay on the target origin, need a safe test environment for login, allow one active scan per project (partial unique index plus a lock), and close stale scans on read and at startup.
- Postgres sessions are short lived and roll back on any error, as AGENTS.md requires. The migrations form one chain with backfills and a sensible downgrade.
- The agent contract is respected. `AccessibilityAgent` logs start and completion, never raises, and owns `accessibility_results`. Dependency injection is used in `build_graph`.
- Docs, changelog, token usage grouping, and the route audit marker were all updated.

## Test coverage
Python coverage is strong: about 2,400 lines across `test_accessibility.py`, `test_accessibility_scans.py`, `test_accessibility_bugs.py`, and `test_batch_run_identity.py`. They cover merge and redaction, gate rules, snapshot handling, the webhook echo cases, repeated webhooks, route permissions, stale scans, bug dedupe and Jira labels, report output, and the JSON and Postgres stores. The autouse fixtures keep tests off the live LLM and the dev database. The gaps are the TypeScript scan code, the engine webhook fields, and the frontend scan dialog (see the minor item above). No test checks how long the webhook takes when the LLM is slow, which is why the major item above is easy to miss.

## Context items you listed
- Recursive screenshot names, the cp1252 text in the batch stream log, and the missing `/testcases/golden-dataset-comparison` route: I did not find a cause in this diff, so I did not re-report them.
- `a11y_enabled` as the string "true": judged above as minor.
