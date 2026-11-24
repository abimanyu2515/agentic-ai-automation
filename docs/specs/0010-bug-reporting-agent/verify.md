# Verify: Bug Reporting Agent · spec 0010 · updated 2026-11-23
_Steps derived from spec 0010 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

Setup:
- Run `alembic upgrade head` (revision `c3f8a1d7e925`) and start the backend.
- Use a project with a target URL and at least one scripted test case.
- To keep Jira untouched, leave the `JIRA_*` env vars unset (mock mode).

## UI / manual
- [ ] Run a test case that fails because of the product → the run's triage says Product Bug, the Executions table shows **Bug report**, and the draft is listed in **Bug Reports** → AC-1, AC-12
- [ ] Open the draft → title, summary, steps, expected and actual result, severity and priority are filled in; Triage evidence shows the failing step, root cause and evidence; no password appears → AC-2, AC-3
- [ ] Re-run the same failure → the same report shows 2 occurrence(s), with no new draft → AC-4
- [ ] Edit the title and severity, click **Submit to Jira** → the report shows its Jira key, and the run's JIRA BUG column shows the key → AC-6, AC-12
- [ ] Open the triage details of a flaky or environment failure → **Report bug** drafts a report and opens it → AC-7, AC-12
- [ ] **Dismiss** a draft with a reason → status Dismissed and the form is read only → AC-8
- [ ] As a Stakeholder → Bug Reports lists reports, but Report bug is hidden and edit or submit is refused → AC-9
- [ ] Project settings: turn on auto-submit at 80% and save → a Product Bug with confidence ≥ 0.8 is submitted with no review → AC-5, AC-14
- [ ] Chat: "report a bug for execution <id>" → a confirm card; Run → a draft and an **Open bug report** link. "submit bug report <id>" → a card; Run → "Filed in Jira as …" → AC-13

## Commands
- [x] `pytest -q backend/tests/test_bug_reports.py backend/tests/test_chat_bug_reports.py` → all pass → AC-1 to AC-11, AC-13, AC-14
- [x] `BUGS_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/bug-reports.spec.ts --project=chromium` (from `backend/`) → 2 passed → AC-12
- [ ] `POST /api/v1/projects/<id>/executions/<passed run>/bug-report` → `409` → AC-7
- [ ] `POST /api/v1/webhooks/jira` with `{"issue": {"key": "<submitted key>", "fields": {"status": {"name": "Resolved"}}}}` and the Jira secret → `resolved_bug_reports: 1`, and the report shows Resolved → AC-10
- [ ] `sync_execution_finished` for a failed run → the Story gets its audit comment and no Bug is created → AC-11
