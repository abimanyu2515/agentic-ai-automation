# Verify: object repository · spec 0004 · updated 2026-11-03
_Steps derived from spec 0004 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [ ] Sign in as a Project Manager, select a project, open `#/object-repository` → page list, element table, health filter, and Scan now are shown → AC-11, AC-13
- [ ] Add page `login_page` with pattern `/login` → it appears in the list with "No elements" → AC-4, AC-13
- [ ] Add page `Bad Name` → the form shows the snake_case error (API 422) → AC-4
- [ ] Add element `login_button` (role `button`, name "Log in", backup css `#login`) → row shows the key, a "Healthy" text badge, the main locator, and "+1 backup" → AC-4, AC-13
- [ ] Edit the element with the keyboard only: Tab to Edit, Enter, move Backup 1 up, Save → main locator is now `css: #login`; Escape closes the drawer and returns focus to Edit → AC-13
- [ ] Filter by "Broken" → "No elements match this health filter." → AC-13
- [ ] Sign in as a Stakeholder → view loads read only (no add, edit, delete, or scan controls) → AC-11
- [ ] On a project not marked as a safe test environment, press Scan now → status says scans need a safe test environment (API 409) → AC-3
- [ ] On a safe test environment project, press Scan now → status moves queued, then running, then shows pages scanned and added, updated, and missing counts; pages appear with health counts → AC-1, AC-3
- [ ] Rescan after editing one recon element by hand and deleting another → the edited element keeps the tester's locators, the deleted one is not recreated, an element no longer on the page shows "Missing" → AC-2, AC-15
- [ ] Generate a Playwright script for an approved test case whose steps visit a mapped page → script uses `await orx(page, '<page>.<element>')` for listed elements → AC-6
- [ ] Open that test case script in the UI → it shows the `// <object-repository>` block with current locators; save it → the stored script has no block (check the database or the JSON store) → AC-8
- [ ] Change the element's main locator, then reopen the script and run it → the new locator appears with no regeneration → AC-8, AC-9
- [ ] Run the test case through the workspace and through the Python runner → element `last_used_at` and `last_execution_id` update; a run that needed a backup sets "Needs review" → AC-9, AC-10
- [ ] Rename the element → response `scripts_rewritten` counts the test cases whose scripts quoted the old key, including a frozen one; scripts now use the new key → AC-5
- [ ] Delete a referenced element → response `referencing_test_cases` is the number of scripts that still use it → AC-5
- [ ] Settings › Audit logs → one `OBJECT_REPOSITORY` entry per create, update, delete (with before and after locators) and one `SCAN` entry per scan → AC-12

## Value sourcing checks
- [ ] Recon page path comes from the inspected URL path only: inspect `/booking/12345/details?x=1` → page pattern `/booking/*/details`, name `booking_details` → AC-1
- [ ] Recon attributes and match counts come from the live page: an element with a duplicated class but a unique id → `css #id` is the main locator; a locator that matches nothing is dropped → AC-1
- [ ] Navigation links come from anchors in `nav` or `[role=navigation]`, saved as `nav` and not also as `link` → AC-1
- [ ] Scan URLs: target URL, plus literal page patterns joined to the target origin, plus URLs named in test case steps; patterns with `*` are not visited → AC-3
- [ ] Scan counts equal the sum of per page `ingest_recon` counts → AC-3
- [ ] Manual edit sets `manually_edited = true`, `health = healthy`, `failure_count = 0` whatever the previous state → AC-4
- [ ] Audit user is the signed in user, not the project owner → AC-12
- [ ] Prompt URLs come from the test case steps plus the target URL path; a case visiting only unmapped pages has no OBJECT REPOSITORY section → AC-6
- [ ] Prompt cap follows `object_repository.max_prompt_elements`: set it to 1 → one element listed and a warning logged → AC-6
- [ ] Render resolves the project from scenario, requirement, project: call `/api/v1/playwright/workspace` with another project's `project_id` → locators still come from the test case's own project → AC-8
- [ ] Locator timeout follows `object_repository.locator_timeout_ms` in the rendered block → AC-9
- [ ] Workspace run markers come from the `completed` payload `logs` (only `[OBJREPO]` lines, at most 2000); Python run markers come from the runner log lines → AC-10
- [ ] The same `completed` webhook sent twice changes counters once (execution ID dedupe) → AC-10

## Commands
- [ ] `pytest -q backend/tests/test_object_repository.py` → all pass (runs the SQL store against the configured database) → AC-1 to AC-12, AC-14, AC-15
- [ ] `uvicorn backend.main:app --port 8000` then `cd backend && OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/object-repository.spec.ts --project=chromium` → passes → AC-13

## Acceptance-criteria coverage
- AC-1 recon scan steps, value sourcing recon checks · AC-2 rescan step · AC-3 scan steps · AC-4 add and invalid page and element steps · AC-5 rename and delete steps · AC-6 generation step, prompt checks · AC-7 `test_unknown_keys_include_run_time_keys` plus the repair loop (generate with an invented key → diagnostic `Unknown object repository keys: ...`) · AC-8 render and strip steps · AC-9 locator change and run step · AC-10 run feedback steps · AC-11 role steps · AC-12 audit step · AC-13 view steps and e2e · AC-14 pytest SQL store tests · AC-15 rescan step

# Verify: object repository, harvest during execution · spec 0004 · updated 2026-11-03
_Steps derived from AC-16 to AC-21 and the harvest rows of the Value sourcing table._

## UI / manual
- [ ] Workspace path: start `uvicorn backend.main:app --port 8000` and `pnpm --dir backend/playwrightt dev`, run a test case that logs in and opens two pages behind login → the `completed` webhook carries `harvest` with both paths, and both pages appear in the Object Repository view → AC-16, AC-18, AC-19
- [ ] Same run with a failing assertion at the end → `status: failed`, `harvest` still sent and ingested → AC-17, AC-18
- [ ] Two workspace executions in a row in one batch cycle (shared page) → each `completed` payload holds only its own pages → AC-16, AC-18
- [ ] Python path: run a test case through the Run button → `backend/playwrightt/artifacts/<test_case_id>/harvest.json` exists and its pages appear in the view → AC-18
- [ ] Batch of two test cases → each run directory holds its own `harvest.json`, redacted with that test case's own test data → AC-18, AC-20
- [ ] A page showing "Welcome <vault username>" and a test data value → neither appears anywhere in the stored repository (search the JSON store or tables) → AC-20
- [ ] An element seen by a run but not by the next run keeps its health; only a scan marks it `missing`; `last_scanned_at` moves only on a scan → AC-19
- [ ] Scan a page and then run a test over the same page → one element per control, not two → AC-21
- [ ] Data driven run of many datasets over the same pages → one snapshot per path; a run over more than `harvest_max_pages` paths stops at the cap → AC-16
- [ ] Set `object_repository.harvest_enabled: false` and restart → `GET /api/v1/object-repository/harvest-settings` says `enabled: false`, no `harvest` field content and no `harvest.json` on either runner → AC-16
- [ ] Stop the backend settings route (or block it) during a workspace run → the run completes normally with harvesting off → AC-17
- [ ] Hash routed page (`/#/cart`) → harvested once as the base path → AC-16

## Commands
- [ ] `pytest -q backend/tests/test_object_repository.py -k "harvest or clean_elements or parse_snapshots"` → all pass → AC-16, AC-18, AC-19, AC-20
- [ ] `pytest -q backend/tests/test_object_harvest_browser.py` (needs Chromium and npx) → extractor counts equal Playwright counts for every strategy, and a real Python runner run harvests each page once across a worker restart with credentials redacted → AC-16, AC-17, AC-18, AC-20, AC-21
- [ ] `cd backend && npx playwright test e2e/object-harvest-session.spec.ts --project=chromium` → passes (settle rule, client routes, cap, shared page, redaction, throwing and hanging extractor, closed page) → AC-16, AC-17, AC-20

## Acceptance-criteria coverage
- AC-16 workspace and Python path steps, data driven step, kill switch, hash route, session spec · AC-17 failing run step, settings outage step, throwing and hanging extractor tests · AC-18 both transports, batch attribution, malformed file tests · AC-19 missing and `last_scanned_at` step, `test_harvest_ingest_adds_and_updates_but_never_marks_missing` · AC-20 redaction step and tests · AC-21 parity test and scan then run step
