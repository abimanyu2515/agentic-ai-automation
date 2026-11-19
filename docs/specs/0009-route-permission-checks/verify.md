# Verify: Permission checks on data changing routes · spec 0009 · updated 2026-11-18
_Steps derived from spec 0009 acceptance criteria and its Value sourcing table. `/check verify` runs these; `/test` locks the durable ones._

Setup: set `WORKSPACE_WEBHOOK_SECRET` in the backend `.env` and in `backend/playwrightt/.env.local` (same value), and `JIRA_WEBHOOK_SECRET` in the backend `.env`. Restart the backend and the workspace. Have three users ready: a Project Manager and a Tester assigned to project A only, and a Stakeholder assigned to project A.

## UI / manual
- [ ] Sign in as the Project Manager, open project A: generate scenarios, edit, duplicate, and delete one, generate test cases and a script, run one test and a batch, create a release and a cycle → each succeeds → AC-2, AC-3
- [x] Sign in as the Stakeholder on project A → no Generate Scenarios, Generate Test Cases, Generate Scripts, Run Selected Batch, Run TestDataAgent, Create Requirement, Import buttons, no Edit, Duplicate, Delete on scenarios, no "+ Create Release" or "+ Create Cycle" options → AC-10
- [ ] As the Tester, open a scenario table → Edit and Duplicate show, Delete is hidden → AC-10
- [x] As the Tester, force a refused action (for example run `API.deleteScenario(...)` from the browser console on project A) → a toast shows the backend's 403 reason, no `alert()` popup, the view stays as it was → AC-10
- [ ] In the workspace (port 3000), open a test case, edit the script, Save → saved with no error; Freeze → the script is frozen → AC-9
- [ ] Start a run from the main UI (Run Selected Batch) → the workspace runs it, the login step gets project A's credentials, and the result shows in Execution History → AC-6

## Commands
- [x] `curl -X POST localhost:8000/api/v1/projects/<A>/requirements/<reqA>/generate-scenarios -H "Content-Type: application/json" -d '{"count":1}'` with no token → `401` → AC-1
- [x] Same call with the Stakeholder's token → `403` `Access Denied: Role 'Stakeholder' does not have 'create' permission for 'Scenarios'` → AC-2
- [x] Project B's generate scenarios with the Tester's token (assigned to A only) → `403` `You do not have access to this project.` → AC-3
- [x] Project A's URL with project B's scenario ID on `DELETE .../scenarios/<scenarioB>` as the Project Manager → `404` `Scenario not found`, project B's scenario still listed → AC-4
- [x] `POST /projects/<A>/batches/execute` with `[<caseA>, <caseB>]` → `404`, no run queued; with `[]` → `422`; with `[<caseA>, <caseA>]` → one run → AC-4
- [x] `POST /scenarios/<scenarioA>/notes` as a user assigned to project B only → `403`; with a random scenario ID → `404` → AC-5
- [x] `GET /projects/<A>/credentials` with no header, a wrong `X-Webhook-Secret`, and the right one → `401`, `401`, `200` with project A's own values; for a project with no stored credentials → `404` → AC-6
- [x] `POST /webhooks/jira?secret=<JIRA_WEBHOOK_SECRET>` with `{"issue":{}}` → `200`; with the header instead → `200`; with a wrong secret → `401`; the uvicorn access line shows `secret=***` → AC-7
- [x] Empty `JIRA_WEBHOOK_SECRET`, restart → startup logs `Webhook secret not configured: JIRA_WEBHOOK_SECRET`, and the Jira webhook returns `503` with that detail → AC-8
- [x] `PUT /playwright/freeze` with `{"test_case_id":"nope"}` → `422` before any auth; `PUT /playwright/script` with project B's ID and project A's test case plus the workspace secret → `404` → AC-9
- [x] Each refused request above → exactly one `Request refused reason=...` warning in the backend log, with the path but no query, token, secret, or body → AC-11
- [x] `pytest -q backend/tests/test_route_audit.py backend/tests/test_route_permissions.py` → all pass → AC-12
- [x] As an Admin without `Settings.edit`: `POST /external-tools`, `DELETE /external-tools/<id>`, `POST /admin/reset-demo-data` → each `403` → AC-13

## Value sourcing
- [x] Role comes from the token, not the request: send the Stakeholder's token with a body field `"role":"Superadmin"` → still `403` → user and role row
- [x] Project role wins over global role: a global Tester assigned to project A as Stakeholder gets `403` on generate scenarios in A → permission check row
- [x] AC-5 projects come from the record: notes on project A's scenario by a project B member → `403`, even though no project ID is sent → AC-5 routes row
- [x] Ownership comes from the parent chain: delete project A's scenario, then execute its test case → `404` → ownership row
- [x] Batch ownership is checked once against the project's test cases: a batch mixing A and B IDs queues nothing → batch row
- [ ] Expected secret comes from `.env`, presented secret from the header (Jira also `?secret=`): change the backend value without changing the workspace → runs stop saving with `401` in the workspace log → secret check rows
- [ ] The workspace sends `WORKSPACE_WEBHOOK_SECRET` from `.env.local`: remove it from `.env.local` and restart the workspace → credentials and webhooks get `401` and the engine logs the status → workspace row
- [x] The toast shows the backend's own `detail` text → 403 toast row
- [x] Each refusal's reason code matches the check that refused it (`no_permission`, `no_project_access`, `bad_secret`) → refusal log row

## Acceptance-criteria coverage
- AC-1: commands 1, `test_every_data_changing_route_needs_a_login_or_a_secret`
- AC-2: UI 1, commands 2, value sourcing 1 and 2
- AC-3: UI 1, commands 3
- AC-4: commands 4 and 5, value sourcing 4 and 5
- AC-5: commands 6, value sourcing 3
- AC-6: UI 6, commands 7, value sourcing 6 and 7
- AC-7: commands 8
- AC-8: commands 9
- AC-9: UI 5, commands 10
- AC-10: UI 2 to 4, value sourcing 8
- AC-11: commands 11, value sourcing 9
- AC-12: commands 12
- AC-13: commands 13
