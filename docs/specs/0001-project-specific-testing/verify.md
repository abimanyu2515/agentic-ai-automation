# Verify: Project specific testing, spec 0001

## Focused fixture update

1. [x] Run `pytest -q backend/tests/test_api.py backend/tests/test_playwright_agent.py`.
   Expect all focused tests to pass. This checks the required project creation fields and explicit Playwright project context.
   Covers AC 1, AC 3, AC 4, and AC 5.

2. [x] Run `git diff --check`.
   Expect no whitespace errors.

3. [x] Run `python -m compileall -q backend`.
   Expect successful backend compilation.

## Project configuration contract

4. [x] Submit a project creation request without `framework` or `target_url`.
   Expect HTTP 422 and no project creation.
   Covers AC 1.

5. [x] Create a project with `framework: playwright` and a valid project URL, then read it.
   Expect `readiness.is_ready` to be true and the normalized URL to be returned.
   Covers AC 2.

6. [ ] Run Playwright generation without explicit project context.
   Expect an explicit validation error, not selection of the first project or a global URL.
   Covers AC 3 and AC 4.

7. [x] Create two projects with equivalent normalized URLs.
   Expect the second creation to return the project URL conflict response.
   Covers AC 7.

---

# Verify: execution carries the project snapshot · spec 0001 · updated 2026-11-17
_Build plan steps 3 and 4, execution path only (single run and batch). Generation, the 423 edit lock, and snapshot reuse on worker recovery are later slices. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [ ] Create a project through the repository with no `target_url`, add an approved test case with a script, then `POST /api/v1/projects/<id>/testcases/<tc>/execute` → `422` with `code: PROJECT_NOT_READY`, `missing_fields: ["target_url"]`, and the "Open Project Settings" action; no execution is saved → AC-3
- [ ] Same legacy project, `POST /api/v1/projects/<id>/batches/execute` with that test case → `422 PROJECT_NOT_READY`, no batch starts → AC-3
- [ ] Ready project (valid URL and `playwright`), run a test case whose script only does `page.goto(process.env.TARGET_URL!)` → the run passes, which proves the runner set `TARGET_URL` → AC-4
- [ ] Read that execution back (`GET` the project's executions, or the `executions` table) → `project_config_snapshot` holds `project_id`, the normalized `target_url`, `framework`, and `credential_reference`, and no username or password → AC-4, AC-8
- [ ] Start the API with `TARGET_URL=https://wrong.example` in its environment, then run the same test case → it still opens the project's URL, never the global one → AC-4
- [ ] In the chat assistant, ask to run a test case of the legacy project → the action ends `failed` with "Project configuration is incomplete." → AC-3

## Commands
- [ ] `python -m alembic current` → `d4e8a1b6c372 (head)`, and `executions.project_config_snapshot` exists as a nullable JSON column → data model
- [ ] `pytest -q backend/tests/test_project_configuration_snapshot.py backend/tests/test_execution_service.py backend/tests/test_workflow_service_langgraph.py` → all pass → AC-4, AC-8

## Value sourcing checks
- [ ] `target_url` in the snapshot → equals `normalize_target_url(project.target_url)` at accept time (for example, `https://Example.com/` is stored as `https://example.com`)
- [ ] `credential_reference` → equals the project ID, and the runner reads the vault with it
- [ ] Immutable snapshot: save the same execution result again with a different snapshot (JSON and PostgreSQL) → the first snapshot is kept → AC-8, AC-12

## Acceptance-criteria coverage (this slice)
- AC-3: execution and batch refusal steps (generation refusal comes in a later slice) · AC-4: TARGET_URL run, global env step, snapshot contents · AC-8: snapshot saved and immutable (the 423 edit lock comes later) · AC-12: JSON and PostgreSQL parity on the immutable snapshot
