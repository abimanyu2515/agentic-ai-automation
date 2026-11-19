# 0009. Check login, permission, and project access on every data changing route

**Date**: 2026-11-18
**Status**: In Progress

## Summary

Today 27 routes that change data accept requests from anyone, and several others check the login but not the permission. That means the UI lets people do things the chat assistant already refuses. This spec puts the same checks on every route that changes data: a valid login, the role permission the chat uses, access to the project, and proof that the record really belongs to that project. The two machine callers (the Playwright workspace and Jira) prove who they are with a shared secret, and the route that returns decrypted project passwords gets locked down too.

## Requirements

**User stories**:
- As a project manager, I want the UI to refuse the same actions the chat refuses, so a role means the same thing everywhere.
- As a Tester assigned to one project, I must not be able to change another project's data by editing a URL.
- As the person running the server, I want only the real workspace and the real Jira to post run results and issue events, and I want project passwords returned only to the workspace.

**Acceptance criteria**:
- **AC-1**: Every `POST`, `PUT`, `PATCH`, and `DELETE` route returns 401 without a valid bearer token. The only exceptions are `POST /auth/login`, `POST /auth/refresh`, and the secret protected machine routes (AC-6, AC-7, AC-9).
- **AC-2**: Each user route checks the module and action listed in the *Route permission map* below. A user without it gets 403 with the existing `Access Denied: Role '<role>' does not have '<action>' permission for '<module>'` message. Where a chat action does the same thing, the route uses the same module and action as the chat catalog.
- **AC-3**: On a project scoped route, the user must have project access (Superadmin, Admin, project owner, or a project assignment). Otherwise the route returns 403. A project that doesn't exist returns 404.
- **AC-4**: Every requirement, scenario, test case, release, or execution named in the path or body must belong to that project. If one doesn't, the route returns 404 for that record type and changes nothing. A test case or scenario whose parent chain (test case → scenario → requirement → project) is broken or deleted counts as not found. A batch with even one foreign test case is refused whole, and nothing is queued. In a batch, an empty list or a malformed ID returns 422 and duplicate IDs are counted once, all before the ownership check.
- **AC-5**: Routes with no `project_id` in the path (scenario notes, test case notes, release cycles, `PUT /playwright/freeze`, `PUT /playwright/script`) resolve the project from the record and then apply AC-2 to AC-4 to that project. A record that doesn't exist returns 404.
- **AC-6**: `POST /projects/{id}/executions/webhook` and `GET /projects/{id}/credentials` accept a request only when its `X-Webhook-Secret` header matches `WORKSPACE_WEBHOOK_SECRET`. Otherwise they return 401. The credentials route returns only that project's own credentials, or 404 when it has none. It never falls back to another project's credentials.
- **AC-7**: `POST /webhooks/jira` accepts the secret from the `X-Webhook-Secret` header or the `secret` query parameter, matched against `JIRA_WEBHOOK_SECRET`. A missing or wrong secret returns 401. The secret value never appears in any log line, the access log included.
- **AC-8**: If a webhook secret env var is empty, its routes return 503 with `Webhook secret not configured: <VAR>`. Startup logs one warning per missing var. Nothing is ever open because a secret is missing.
- **AC-9**: `PUT /playwright/freeze` and `PUT /playwright/script` accept either a signed in user (who passes AC-2 to AC-5 with `TestCases.edit`) or the workspace secret. A malformed `test_case_id` or `project_id` returns 422 before any secret or user check. On `/playwright/script`, a body `project_id` that doesn't own the test case returns 404. The workspace page calls these through its own Next.js API routes, which add the secret on the server. Freezing and saving work from the workspace (saving is broken today by an `UnboundLocalError` in the route, and this fixes it).
- **AC-10**: When the main UI gets a 403, it shows the backend's reason in a toast and leaves the current view unchanged, with no `alert()` popup on top. Buttons for the generate, execute, scenario edit, delete and duplicate, requirement create and import, and release actions are hidden when the role lacks their permission.
- **AC-11**: Every refusal (401 from a bad secret, 403 for permission, access, or project) writes one warning log line with user ID (`machine` for a secret caller), role (`workspace` or `jira` for a secret caller), method, path (`request.url.path`, never the query string), module and action, project ID, and a reason code (`no_permission`, `no_project_access`, `bad_secret`). It never includes a secret, token, or request body.
- **AC-12**: A pytest route audit fails when any data changing route has neither a user dependency (`get_current_user`) nor a secret dependency, unless it's on the explicit allow list (login and refresh).
- **AC-13**: `POST` and `DELETE /external-tools` and `POST /admin/reset-demo-data` require `Settings.edit`. Demo reset no longer checks a hardcoded role list.

## Decision

**Chosen option**: Option 1: Fix in place with shared FastAPI dependencies.

Add the checks route by route using the helpers that already exist (`get_current_user`, `has_permission`, `check_permission`, `require_project_access`), plus two small new dependencies for webhook secrets and one helper for routes whose project comes from the record. A route audit test keeps new routes honest.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

**Data model sketch**: no schema change. Permissions reuse the seeded modules (`Requirements`, `Scenarios`, `TestCases`, `Playwright`, `Executions`, `Users`, `Settings`, `ObjectRepository`). No seed or migration change is needed.

**Route permission map** (all paths under `/api/v1`; "access" means `require_project_access`; "owns" means the AC-4 check):

| Route | Method | Permission | Extra checks |
|---|---|---|---|
| `/projects/{project_id}/requirements` | POST | `Requirements.create` | access |
| `/projects/{project_id}/requirements/import` | POST | `Requirements.create` | access |
| `/projects/{project_id}/requirements/import-jira` | POST | `Requirements.create` | access |
| `/projects/{project_id}/requirements/{requirement_id}/generate-backlog` | POST | `Requirements.create` † | access, owns requirement |
| `/projects/{project_id}/requirements/{requirement_id}/sync-jira` | POST | `Requirements.edit` † | access, owns requirement |
| `/projects/{project_id}/requirements/{requirement_id}/generate-scenarios` | POST | `Scenarios.create` | access, owns requirement |
| `/projects/{project_id}/scenarios/{scenario_id}` | PUT | `Scenarios.edit` | access, owns scenario |
| `/projects/{project_id}/scenarios/{scenario_id}` | DELETE | `Scenarios.delete` | access, owns scenario |
| `/projects/{project_id}/scenarios/duplicate/{scenario_id}` | POST | `Scenarios.create` | access, owns scenario |
| `/scenarios/{scenario_id}/notes` | POST | `Scenarios.edit` † | project from scenario (AC-5) |
| `/projects/{project_id}/requirements/{requirement_id}/generate-testcases` | POST | `TestCases.create` | access, owns requirement |
| `/projects/{project_id}/requirements/{requirement_id}/generate-testdata-tests` | POST | `TestCases.create` † | access, owns requirement |
| `/projects/{project_id}/requirements/{requirement_id}/testcases/{test_case_id}/generate-testdata` | POST | `TestCases.edit` | access, owns requirement and test case |
| `/projects/{project_id}/requirements/{requirement_id}/testcases/{test_case_id}/generate-testdata-script` | POST | `Playwright.create` † | access, owns requirement and test case |
| `/testcases/{test_case_id}/notes` | POST | `TestCases.edit` † | project from test case (AC-5) |
| `/projects/{project_id}/testcases/{test_case_id}/generate-script` | POST | `Playwright.create` | access, owns test case |
| `/projects/{project_id}/testcases/{test_case_id}/generate-script/jobs` | POST | `Playwright.create` | access, owns test case |
| `/projects/{project_id}/import-framework` | POST | `Playwright.create` † | access |
| `/playwright/freeze` | PUT | `TestCases.edit` or workspace secret | project from test case (AC-5, AC-9) |
| `/playwright/script` | PUT | `TestCases.edit` or workspace secret | body `project_id` must own the test case (AC-9) |
| `/projects/{project_id}/testcases/{test_case_id}/execute` | POST | `Executions.create` | access, owns test case |
| `/projects/{project_id}/batches/execute` | POST | `Executions.create` | access, owns every test case (all or nothing) |
| `/projects/{project_id}/executions/{execution_id}/sync-bug` | POST | `Executions.edit` † | access, owns execution |
| `/projects/{project_id}/releases` | POST | `Executions.create` † | access |
| `/releases/{release_id}/cycles` | POST | `Executions.create` † | project from release (AC-5) |
| `/projects/{project_id}/users` | POST | `Users.edit` (unchanged) | add access |
| object repository `POST`, `PATCH`, `DELETE` routes | all | unchanged `ObjectRepository.*` | add access |
| `/external-tools`, `/external-tools/{tool_id}` | POST, DELETE | `Settings.edit` | none (global) |
| `/admin/reset-demo-data` | POST | `Settings.edit` | none (global), drop the hardcoded role list |
| `/projects/{project_id}/executions/webhook` | POST | workspace secret | none |
| `/projects/{project_id}/credentials` | GET | workspace secret | own project only, 404 when none |
| `/webhooks/jira` | POST | Jira secret (header or `?secret=`) | none |

Rows marked † have no chat action to copy, so their module and action are a judgment call (the closest module, the narrowest action that fits). Question these first if a role behaves unexpectedly: generate backlog, sync Jira, generate test data tests, test data script, import framework, notes, sync bug, releases, and cycles.

Routes already checked (test case edit and delete, script edit, execution deletes, settings, project create, edit and delete, knowledge, admin users, roles, permissions) keep their current checks. Chat thread delete, action confirm, and action cancel check login and project access only. That's correct: confirm checks the action's own permission again before it runs (spec 0005). `PUT /token-usage/settings` already gets the user through `has_permission` and stays as it is.

**New backend pieces** (in `backend/dependencies/auth_dependencies.py` unless noted):
- `require_workspace_secret` and `require_jira_secret`: FastAPI dependencies. Each reads its secret from settings, returns 503 when empty (AC-8), compares with `hmac.compare_digest` on both values encoded to UTF-8 bytes (a `str` compare raises on non ASCII input and would turn a bad secret into a 500), returns 401 on a mismatch, and logs the refusal (AC-11). The Jira one reads the header first, then the `secret` query parameter.
- `workspace_or_user`: a dependency for freeze and script. Both routes first parse their body into a small Pydantic model with `UUID` fields, so a malformed ID is a 422 before any auth branch (AC-9). A valid workspace secret returns a machine marker. Otherwise it falls back to `get_current_user`, and the route then runs the AC-5 checks with `check_permission` and `require_project_access`.
- `authorize_project(user, db, project_id, module, action)`: a plain helper for AC-5 routes. It runs `check_permission` (raising 403 with the AC-2 message), then `require_project_access`. It's the same order as path based routes, so both refuse the same way.
- Ownership helpers in `backend/main.py` beside `_require_test_case_in_project`: `_require_requirement_in_project`, `_require_scenario_in_project`, `_require_execution_in_project`, plus `_project_of_test_case` and `_project_of_scenario` for AC-5 routes. A test case resolves through its scenario, then the requirement, then the project, because neither model stores a `project_id`. A missing or deleted link anywhere in that chain is a 404 (AC-4). They read through the repository, never by loading every project.
- A refusal logger, `log_refusal(user, request, module, action, project_id, reason)`, called from `has_permission`, `require_project_access`, `authorize_project`, and the secret dependencies (AC-11).
- An access log filter attached to the `uvicorn.access` logger at startup. Uvicorn keeps the request path with its query in `record.args[2]`, not in `record.msg`, so the filter rewrites `secret=<value>` to `secret=***` in that argument (AC-7).
- Fix `PUT /playwright/script`: remove the `from uuid import UUID` inside the function, which runs after `UUID` is used and raises `UnboundLocalError` today (AC-9).
- A startup check that logs one warning per empty secret var (AC-8).

**Workspace pieces** (`backend/playwrightt`):
- `lib/execution-engine.ts`: send `X-Webhook-Secret: process.env.WORKSPACE_WEBHOOK_SECRET` on the run webhook and the credentials call. Treat a 404 from credentials as "no credentials". Treat a 401 or 503 the same way and log the status, so a missing secret fails the login step clearly instead of crashing the run (AC-6).
- New `app/api/playwright/freeze/route.ts` and `app/api/playwright/script/route.ts`: server routes that forward the body to `${process.env.BACKEND_URL}` with the secret header and pass the status and body back. `BACKEND_URL` defaults to `http://127.0.0.1:8000`; the browser side `getBackendUrl()` can't be used on the server. `app/page.tsx` calls these instead of FastAPI directly (AC-9).

**Frontend pieces**:
- `frontend/js/components.js`: a small `showToast(message, kind)` (one fixed container with `role="status"` and `aria-live="polite"`, auto dismissed after 6 seconds, text set with `textContent`).
- `frontend/js/api.js` `request`: on a 403, call `showToast(detail, 'error')`, then throw the error with `error.shown = true`.
- The catch blocks for the AC-10 actions in `frontend/js/app.js` skip their `alert()` when `err.shown` is true. Other catch blocks are left alone.
- `enforceUIPermissions`: hide the AC-10 buttons with `window.hasPermission(module, action)` from the route map.

**Order of checks** for a user route: 401 (no login) → 403 (no permission) → 404 (no project) → 403 (no project access) → 404 (record not in project) → validation 422 and work. A refused request starts no generation, execution, or queued job.

**Value sourcing**:
| Action | Value | Source |
|---|---|---|
| Any user route | user and role | bearer token through `get_current_user` |
| Permission check | project role, else global role | `check_permission` (spec 0005) with the path `project_id`, or the project resolved per AC-5 |
| AC-5 routes | project ID | scenario → requirement → `project_id`; test case → scenario → requirement → `project_id`; release → `project_id`; `/playwright/script` → body `project_id`, checked against the test case |
| Ownership (AC-4) | the record's project | the repository record's parent chain, same as AC-5 |
| Batch (AC-4) | the set of test case IDs | request body, checked against `project_service.get_test_cases_for_project(project_id)` once per request |
| Secret check | expected secret | `WORKSPACE_WEBHOOK_SECRET` or `JIRA_WEBHOOK_SECRET` in settings (`.env` first) |
| Secret check | presented secret | `X-Webhook-Secret` header; Jira also `secret` query parameter |
| Workspace | secret it sends | `process.env.WORKSPACE_WEBHOOK_SECRET` from `backend/playwrightt/.env.local` |
| 403 toast | message | response `detail` from the AC-2 or `require_project_access` message |
| Refusal log | reason code | set by whichever check refused |

**Key invariants**:
- No data changing route runs its body before every check for that route has passed.
- A check never depends on the client: role, project, and ownership come from the token and the repository, never from a body field the caller sets (the `/playwright/script` `project_id` is checked against the test case, never trusted alone).
- A missing secret means closed (503), never open.
- The credentials route never returns another project's credentials.

**Security model**: roles and project access follow the existing RBAC (spec 0005 `check_permission`, project role before global role, Superadmin always allowed). Machine callers get exactly the routes in the map and nothing else. A workspace secret holder can freeze and save any test case script; that matches today's local only workspace and is called out under Consequences. Secrets live in `.env` files only, and are compared in constant time.

**Configuration required**:
- `WORKSPACE_WEBHOOK_SECRET`: shared by the backend `.env` and `backend/playwrightt/.env.local`. Gates the run webhook, credentials, and workspace freeze and script. Generate it with a 32 byte random value.
- `JIRA_WEBHOOK_SECRET`: backend `.env` only. Gates the Jira webhook. Put it in the Jira webhook URL as `?secret=` or in a Jira Automation header.
- `BACKEND_URL`: `backend/playwrightt/.env.local` only. Where the workspace server routes forward freeze and script. Defaults to `http://127.0.0.1:8000`.
- Both secrets are documented in `.env.example`. The workspace secret and `BACKEND_URL` are also documented in a new `backend/playwrightt/.env.example`. Both are read through `backend/config/settings.py`.

**Critical test scenarios**:
- Happy path: a Project Manager on the project generates scenarios, edits, duplicates and deletes a scenario, executes one test case and a batch, and creates a release and cycle. Each returns 200 or 201. Verifies **AC-2**, **AC-3**.
- No login: each route in the map returns 401 with no token. Verifies **AC-1**.
- Role: a Stakeholder gets 403 on generate scenarios and execute. A Tester gets 403 on scenario delete. Verifies **AC-2**.
- Wrong project: a Tester assigned only to project A gets 403 on every project B route. Verifies **AC-3**.
- Foreign record: project A's URL with project B's scenario, test case, requirement, or execution returns 404 and changes nothing. A batch with one foreign test case queues nothing. An empty batch or a malformed ID returns 422. A test case whose scenario was deleted returns 404. Verifies **AC-4**.
- No path project: notes, cycles, freeze, and script resolve the project and refuse an outsider with 403. Verifies **AC-5**.
- Secrets: the run webhook and credentials return 401 without a secret or with a wrong one, and 200 with the right one. Credentials for a project without its own returns 404, never another project's. Verifies **AC-6**.
- Jira: the header and the query parameter both work. A wrong secret returns 401. The access log record shows `secret=***`. Verifies **AC-7**.
- Unset secret: 503 with the var name, and the startup warning is logged. Verifies **AC-8**.
- Workspace: freeze and script work with the secret and with an editor's token, and return 403 for a Stakeholder token. Saving a script succeeds (the `UnboundLocalError` regression). A malformed ID returns 422, and a body `project_id` that doesn't own the test case returns 404. Verifies **AC-9**.
- Secrets edge: a non ASCII secret value returns 401, not 500. Verifies **AC-6**, **AC-7**.
- UI: a 403 shows the toast, and no alert. A Stakeholder sees no generate or execute buttons. Verifies **AC-10**.
- Logging: a refusal writes one warning with the reason code and no secret or body. Verifies **AC-11**.
- Audit: adding an unauthenticated `POST` route in a test app makes the audit fail. Verifies **AC-12**.
- Global: an Admin without `Settings.edit` gets 403 on external tools and demo reset. Verifies **AC-13**.

## Build plan

Tracer Bullet: the first slice carries one route through every layer (dependency, ownership, log, toast, test, audit), then the rest thicken it.

1. Thin thread on generate scenarios: add `log_refusal`, `_require_requirement_in_project`, and the user, permission, access, and ownership checks to `POST .../generate-scenarios`. Add the sign in fixture to `backend/tests/conftest.py`, the `showToast` component, the 403 path in `api.js`, the `err.shown` skip in its catch, and its grid test, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**, **AC-10**, **AC-11**
2. Route audit test with the allow list, marking the still open routes as a shrinking `pending` list that later tasks empty, satisfies **AC-12**
3. Scenario edit, delete, duplicate, generate test cases, generate test data, test data script, generate script, script jobs, and import framework, with `_require_scenario_in_project` and grid tests, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**
4. Execute and batch execute (all or nothing ownership), sync bug with `_require_execution_in_project`, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**
5. Requirements create, import, Jira import, generate backlog, sync Jira; releases; project users and object repository access, satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**
6. `authorize_project` and the AC-5 routes: scenario notes, test case notes, release cycles, satisfies **AC-5**
7. Global admin routes: external tools and demo reset on `Settings.edit`, satisfies **AC-13**
8. Secrets: settings fields, `require_workspace_secret`, `require_jira_secret`, the startup warning, the access log filter, and both `.env.example` files. In `backend/tests/conftest.py`, add a settings override that sets both secrets, plus a `webhook_headers()` helper, and update the tests that post to the webhooks with no secret (`test_playwright_webhook.py`, `test_object_repository.py`, `test_knowledge_defects.py`, `test_jira.py`), satisfies **AC-6**, **AC-7**, **AC-8**, **AC-11**
9. Run webhook and credentials behind the workspace secret; remove the cross project credentials fallback; the engine sends the header and handles 404, satisfies **AC-6**
10. Jira webhook behind the Jira secret, satisfies **AC-7**
11. Fix the `UnboundLocalError` in `PUT /playwright/script` with a regression test first. Then add the body models, `workspace_or_user` on freeze and script, the two Next.js proxy routes using `BACKEND_URL`, and switch `page.tsx` to them, satisfies **AC-9**
12. Hide the AC-10 buttons in `enforceUIPermissions`, and add the `err.shown` skip to their catch blocks, satisfies **AC-10**
13. Empty the audit `pending` list. Update `docs/api.md` (auth per route, the secret header, the 503 and 401 cases) and the setup docs for both secrets, satisfies **AC-12**, **AC-6**, **AC-7**

## Migration plan

**Strategy**: no data migration. One coordinated config change.
**Phases**:
1. Tasks 1 to 7 ship together or one by one. They need no config, and the UI already sends tokens.
2. Tasks 8 to 11 need both `.env` files set before the backend restarts. Set `WORKSPACE_WEBHOOK_SECRET` in both files and `JIRA_WEBHOOK_SECRET` in the backend `.env`, update the Jira webhook URL, then restart the backend and the workspace.
**Rollback**: revert the commit. Leaving the secrets in `.env` is harmless.
**Risks**: a workspace started without `.env.local` gets 401 on every run webhook, so results stop saving (the startup warning and AC-11 logs show why). A Jira webhook left on the old URL gets 401 until it's updated.

## Consequences

**Positive**:
- A role means the same thing in the UI and the chat, and project membership finally limits what a user can change.
- Decrypted passwords and the run result feed are no longer open to anyone who can reach the port.
- The audit test stops the next unchecked route at CI.

**Negative / tradeoffs**:
- About 30 existing API tests need the sign in fixture.
- Two secrets to set and keep in sync, and a fresh setup fails closed until they're set.
- Anyone who can open the workspace (port 3000) can still freeze and save scripts through the proxy. The workspace's own `/api/execute` is just as open today, so port 3000 as a whole is a trusted local surface. This is acceptable only while the workspace stays local only.
- Admins lose demo reset unless an admin grants them `Settings.edit`.
- `?secret=` sits in the Jira webhook URL, which Jira admins can see.

**Neutral**:
- GET routes stay open (46 today) apart from credentials. That's a separate feature.
- The toast is new UI, used only for 403 for now.

## Follow-up

- [ ] Enroll a feature for login and project checks on the read (GET) routes, including `GET /settings`, which the workspace also calls.
- [ ] If the workspace ever leaves localhost, protect the whole workspace (its pages, `/api/execute`, and the proxy) with a per user token handoff (option recorded in rationale.md).
- [ ] The Jira webhook still scans every project per event (review 2026-11-18). Now that it's authenticated it's lower risk, but still worth fixing.
