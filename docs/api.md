# API Reference

Base URL: `/api/v1`

Authentication: JWT Bearer token in `Authorization` header.

## Access control on data changing routes

Spec 0009. Every `POST`, `PUT`, `PATCH`, and `DELETE` route checks its caller. The only open ones are `POST /auth/login` and `POST /auth/refresh`. A pytest route audit (`backend/tests/test_route_audit.py`) fails when a new data changing route has no login or secret check.

**User routes** run their checks in this order, and a refused request starts no generation, execution, or queued job:

| Order | Check | Result when it fails |
|---|---|---|
| 1 | A valid bearer token | `401` |
| 2 | The route's module and action (project role first, then global role; Superadmin always) | `403` `Access Denied: Role '<role>' does not have '<action>' permission for '<module>'` |
| 3 | The project exists | `404` `Project not found` |
| 4 | Project access: Superadmin, Admin, the project owner, or a project assignment | `403` `You do not have access to this project.` |
| 5 | Every requirement, scenario, test case, or execution named in the path or body belongs to the project | `404` `<Record> not found` |
| 6 | Body validation | `422` |

A test case resolves to its project through scenario, requirement, project. A broken or deleted link anywhere in that chain counts as not found. Routes with no `project_id` in the path (scenario notes, test case notes, release cycles, freeze, script) first resolve the project from the record (`404` when the record is missing), then run checks 2 and 4 against it.

| Route | Permission |
|---|---|
| `POST /projects/{id}/requirements`, `/requirements/import`, `/requirements/import-jira` | `Requirements.create` |
| `POST /projects/{id}/requirements/{rid}/generate-backlog` | `Requirements.create` |
| `POST /projects/{id}/requirements/{rid}/sync-jira` | `Requirements.edit` |
| `POST /projects/{id}/requirements/{rid}/generate-scenarios` | `Scenarios.create` |
| `PUT /projects/{id}/scenarios/{sid}` | `Scenarios.edit` |
| `DELETE /projects/{id}/scenarios/{sid}` | `Scenarios.delete` |
| `POST /projects/{id}/scenarios/duplicate/{sid}` | `Scenarios.create` |
| `POST /scenarios/{sid}/notes` | `Scenarios.edit` |
| `POST /projects/{id}/requirements/{rid}/generate-testcases`, `/generate-testdata-tests` | `TestCases.create` |
| `POST /projects/{id}/requirements/{rid}/testcases/{tid}/generate-testdata` | `TestCases.edit` |
| `POST /projects/{id}/requirements/{rid}/testcases/{tid}/generate-testdata-script` | `Playwright.create` |
| `POST /testcases/{tid}/notes` | `TestCases.edit` |
| `POST /projects/{id}/testcases/{tid}/generate-script`, `/generate-script/jobs` | `Playwright.create` |
| `POST /projects/{id}/import-framework` | `Playwright.create` |
| `PUT /playwright/freeze`, `PUT /playwright/script` | `TestCases.edit`, or the workspace secret |
| `POST /projects/{id}/testcases/{tid}/execute`, `POST /projects/{id}/batches/execute` | `Executions.create` |
| `POST /projects/{id}/executions/{eid}/sync-bug` | `Executions.edit` |
| `POST /projects/{id}/releases`, `POST /releases/{rid}/cycles` | `Executions.create` |
| `POST /projects/{id}/users` | `Users.edit` plus project access |
| Object repository `POST`, `PATCH`, `DELETE` | `ObjectRepository.*` plus project access |
| `POST`, `DELETE /external-tools`, `POST /admin/reset-demo-data` | `Settings.edit` (global) |

`POST /projects/{id}/batches/execute`: an empty list or a malformed ID is `422`, and duplicate IDs count once, all before the ownership check. One test case from another project refuses the whole batch with `404`, and nothing is queued.

Spec 0012: the batch start takes an optional `?test_cycle_id=<uuid>`. Every run of the batch is saved with that cycle and with the `batch_id` the response returns (`{"status": "started", "batch_id": "uuid"}`). A cycle that doesn't exist, or whose release belongs to another project, is `404 Test cycle not found`, and nothing is queued. Without it, the runs have no cycle.

`GET /projects/{id}/batches/{batch_id}/html` builds one HTML report from the runs saved with that `batch_id` in this project. It needs `Executions.view` and project access through the Bearer token (there is no cookie), so a plain browser link gets `401`. Another project's batch, or a batch with no runs, is `404` (`No executions found for this batch`). Any other failure is logged and returns `500` without the error text. Every value in the report is HTML escaped, and the report has no script. The executions UI fetches it with the token and opens it as a `blob:` page with a `<base>` tag and a `Content-Security-Policy` `<meta>` (`default-src 'none'`), so a reload or bookmark of that page doesn't work; you reopen it from the app.

`GET /projects/{id}/batches/{batch_id}/stream` sends the batch's event log as server sent events, from its first event, and ends after `Completed` or `Error`. It has no auth (a browser `EventSource` can't send a Bearer token), so it streams only a backend batch whose log was started by this project. For anything else (an unknown id, an id that isn't a UUID, another project's batch, or a batch from before a server restart) it never answers with an HTTP error. It sends one event and ends:

```json
{"status": "Error", "untracked": true, "log": "This batch is not tracked on this server. It may belong to another project, or the server restarted."}
```

The live observer (`#/execution/BATCH-<batch_id>`) shows no report button for that event.

**Machine routes** prove the caller with a shared secret in the `X-Webhook-Secret` header, compared in constant time:

| Route | Secret |
|---|---|
| `POST /projects/{id}/executions/webhook` | `WORKSPACE_WEBHOOK_SECRET` |
| `GET /projects/{id}/credentials` | `WORKSPACE_WEBHOOK_SECRET` |
| `PUT /playwright/freeze`, `PUT /playwright/script` | `WORKSPACE_WEBHOOK_SECRET`, or a signed in user |
| `POST /webhooks/jira` | `JIRA_WEBHOOK_SECRET`, from the header or the `secret` query parameter |

A missing or wrong secret returns `401`. An empty secret variable returns `503` `Webhook secret not configured: <VAR>` (the routes stay closed), and startup logs one warning per empty variable. The access log masks `?secret=` as `secret=***`.

**Refusal log**: every `401` from a bad secret and every `403` writes one warning: `Request refused reason=<no_permission|no_project_access|bad_secret> user_id=<id or machine> role=<role, workspace, or jira> method=<method> path=<path without query> module=<module> action=<action> project_id=<id>`. It never includes a secret, token, or request body.

**UI**: a `403` shows the backend's reason in a toast, and the view stays as it was. Buttons for generate, execute, scenario edit, delete and duplicate, requirement create and import, and release actions are hidden when the signed in role lacks their permission. The UI reads the role's permissions from `GET /api/v1/auth/me`, which any signed in user may call: it returns `id`, `email`, `full_name`, `role`, and `permissions`, a sorted list of `Module.action` strings for the user's global role (every permission for Superadmin). Hiding a button is only a convenience; the backend checks every request.

---

## Projects

### List Projects

```
GET /api/v1/projects
```

Returns all projects with decrypted credential usernames (passwords never exposed).

**Response**: `200` — Array of `ProjectResponse`

```json
[
  {
    "id": "uuid",
    "name": "string",
    "description": "string",
    "line_of_business": "string",
    "framework": "playwright",
    "created_at": "datetime",
    "requirements": ["uuid", ...],
    "jira_project_key": "string|null",
    "target_url": "string",
    "target_username": "string|null"
  }
]
```

---

### Create Project

```
POST /api/v1/projects
```

**Request Body** (`ProjectCreate`):

```json
{
  "name": "My Project",
  "description": "Project description",
  "line_of_business": "general",
  "framework": "playwright",
  "jira_project_key": null,
  "target_url": "https://your-app.example.com/"
}
```

**Auth**: a signed in user with the `Projects:create` permission. The server sets `owner_id` and `created_by` from that user (spec 0002).

**Validation**: `name` and `description` must be non empty. `framework` must be a supported framework. `target_url` is required and must be an absolute HTTP or HTTPS URL with no userinfo and no fragment (query text is allowed). It is normalized before the duplicate check and storage. `target_username`, `target_password`, `owner_id`, and any other undeclared field are rejected; add credentials later through Project Settings.

**Response**: `201` with `ProjectResponse`, including the normalized `target_url`, `jira_project_key`, and `readiness: {is_ready, missing_fields, invalid_fields}`.

**Errors**:

* `401` not signed in, `403` missing `Projects:create`. Nothing is saved.
* `422` with the `ProjectConfigurationError` shape (`code`, `message`, `missing_fields`, `invalid_fields`, `action`). A forbidden field shows as `invalid_fields.<field>: "field_not_permitted"`.
* `409` when the same owner already has an active project with this normalized URL. Concurrent duplicates give one `201` and one `409`:

```json
{
  "code": "PROJECT_URL_CONFLICT",
  "message": "A project with this target URL already exists for the current owner.",
  "invalid_fields": {"target_url": "duplicate_url"},
  "conflicting_project_id": "uuid"
}
```

* `500` with `{"code": "PROJECT_PERSISTENCE_FAILED", "message": "..."}` for an unexpected storage failure. The transaction is rolled back and no project is left behind.

Creation logs record the project and owner IDs, and the URL with its query text redacted.

---

### Get Project

```
GET /api/v1/projects/{project_id}
```

**Response**: `200` — `ProjectResponse`, `404` if not found.

---

### Update Project

```
PUT /api/v1/projects/{project_id}
```

**Request Body** (`ProjectUpdate`): All fields optional.

```json
{
  "name": "Updated Name",
  "description": "Updated description",
  "line_of_business": "banking",
  "framework": "selenium",
  "jira_project_key": "PROJ",
  "target_url": "https://example.com",
  "target_username": "newuser",
  "target_password": "newpass"
}
```

Credentials are stored in encrypted vault. Password `"********"` is ignored (no update).

Optional switches: `is_safe_test_environment`, `bug_auto_submit` with `bug_auto_submit_min_confidence` (0 to 1), and `self_healing_enabled` (spec 0011; `true` by default, `false` turns self healing locators off for the project). `ProjectResponse` returns each of them.

**Response**: `200` — `ProjectResponse`, `404` if not found.

---

### Delete Project

```
DELETE /api/v1/projects/{project_id}
```

**Response**: `200` — `{"detail": "Project deleted successfully"}`, `404` if not found.

**Access**: a signed in user (`401`) with access to the project (`403`) and the `Projects` `delete` permission (`403`).

Deleting a project also removes its uploaded knowledge files (`<RAG_STORAGE_ROOT>/<project_id>/`) right away, and deletes its RAGFlow dataset in the background. A failed dataset delete is logged and never blocks the project delete.

---

## Requirements

### List Requirements

```
GET /api/v1/projects/{project_id}/requirements
```

**Response**: `200` — Array of `RequirementResponse`

```json
[
  {
    "id": "uuid",
    "title": "string",
    "description": "string",
    "source": "manual|pdf|jira",
    "uploaded_at": "datetime",
    "priority": "string",
    "business_domain": "string",
    "attachments": ["string"],
    "original_filename": "string|null",
    "requirement_id": "string|null",
    "requirement_title": "string|null",
    "jira_issue_key": "string|null",
    "jira_issue_url": "string|null",
    "jira_sync_status": "string|null",
    "jira_last_synced_at": "datetime|null",
    "release_id": "uuid|null"
  }
]
```

---

### Create Requirement (Manual)

```
POST /api/v1/projects/{project_id}/requirements
```

**Request Body** (`RequirementCreate`):

```json
{
  "title": "User Login",
  "description": "User should be able to login with valid credentials",
  "priority": "high",
  "business_domain": "authentication",
  "release_id": null
}
```

Every call adds a new requirement. The server assigns the next unused `REQ-NNN` key in the project, so manual requirements never replace each other or imported ones.

**Response**: `201` — `RequirementResponse`
**Error**: `400` if validation fails, `404` if project not found.

---

### Import Requirement File

```
POST /api/v1/projects/{project_id}/requirements/import
Content-Type: multipart/form-data

file: <upload_file>
release_id: (optional UUID query param)
```

Supported file formats: PDF, DOCX, XLSX, CSV, TXT.

Parses file into requirement blocks, creates draft requirements, then runs `INGEST` LangGraph workflow for LLM enrichment.

Imports always add requirements and never replace existing ones. A block keeps its requirement ID from the file when that ID is unused in the project. A reused `REQ-NNN` ID (or a row with no ID) gets the next unused `REQ-NNN` key, and any other reused ID gets a numeric suffix such as `PAY-7-2`.

**Response**: `200` — Array of `RequirementResponse`
**Error**: `500` if parsing or ingestion fails.

---

### Import JIRA Story

```
POST /api/v1/projects/{project_id}/requirements/import-jira?issue_key=QA-101&release_id=optional-uuid
```

Requires JIRA authentication. Creates requirement from JIRA issue fields (summary, description, priority), then runs `INGEST` workflow.

**Response**: `200` — `RequirementResponse`
**Error**: `404` if issue not found or inaccessible.

---

## Scenarios

### List Scenarios

```
GET /api/v1/projects/{project_id}/scenarios
```

Returns all scenarios for the project across all requirements.

**Response**: `200` — Array of `ScenarioResponse`

```json
[
  {
    "id": "uuid",
    "requirement_id": "uuid",
    "scenario_name": "string",
    "description": "string",
    "priority": "string",
    "confidence": 0.95,
    "approved": false,
    "rejected": false,
    "generated_at": "datetime",
    "reviewer": "string|null",
    "approved_at": "datetime|null",
    "jira_issue_key": "string|null",
    "jira_issue_url": "string|null",
    "jira_sync_status": "string|null",
    "jira_last_synced_at": "datetime|null"
  }
]
```

---

### Generate Scenarios

```
POST /api/v1/projects/{project_id}/requirements/{requirement_id}/generate-scenarios
```

Triggers ScenarioAgent via LangGraph.

**Request Body**:

```json
{
  "count": 3,
  "mode": "append"
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `count` | int | 3 | Number of scenarios to generate (1-50) |
| `mode` | string | "append" | `"append"` keeps existing; `"replace"` clears first |

**Response**: `200` — Array of `ScenarioResponse`
**Error**: `404` if requirement not found, `500` on generation failure.

---

### Update Scenario

```
PUT /api/v1/projects/{project_id}/scenarios/{scenario_id}
```

Used for approval, rejection, or content edits.

**Request Body** (`ScenarioUpdate`):

```json
{
  "scenario_name": "Updated Name",
  "description": "Updated description",
  "priority": "high",
  "approved": true,
  "rejected": false
}
```

**Response**: `200` — `ScenarioResponse`, `404` if not found.

---

### Delete Scenario

```
DELETE /api/v1/projects/{project_id}/scenarios/{scenario_id}
```

**Response**: `200` — `{"detail": "Scenario deleted successfully"}`

---

### Duplicate Scenario

```
POST /api/v1/projects/{project_id}/scenarios/duplicate/{scenario_id}
```

Creates a copy with new UUID and generated_at timestamp.

**Response**: `200` — `ScenarioResponse`

---

### Generate Backlog

```
POST /api/v1/projects/{project_id}/requirements/{requirement_id}/generate-backlog
```

Runs BacklogCreationAgent → QAStoryAnalyzerAgent → ScenarioAgent pipeline.

**Response**: `200` — Array of `ScenarioResponse`

---

## Test Cases

### List Test Cases

```
GET /api/v1/projects/{project_id}/testcases
```

**Response**: `200` — Array of `TestCaseResponse`

```json
[
  {
    "id": "uuid",
    "scenario_id": "uuid",
    "title": "string",
    "preconditions": ["string"],
    "steps": ["string"],
    "expected_result": "string",
    "priority": "string",
    "status": "string",
    "confidence": 0.95,
    "evaluation_status": "approved|needs_review|rejected|duplicate",
    "evaluation_reason": "string|null",
    "playwright_script": "string|null",
    "groundedness_score": "number|null",
    "unsupported_claims": ["string"],
    "generated_at": "datetime",
    "jira_issue_key": "string|null",
    "jira_issue_url": "string|null",
    "jira_sync_status": "string|null",
    "jira_last_synced_at": "datetime|null"
  }
]
```

---

### Generate Test Cases

```
POST /api/v1/projects/{project_id}/requirements/{requirement_id}/generate-testcases
```

Triggers TestCaseAgent → EvaluationAgent → HumanApprovalAgent pipeline via LangGraph.

**Prerequisites**: At least one approved scenario for the requirement.

**Response**: `200` — Array of `TestCaseResponse`
**Error**: `400` if no approved scenarios, `500` on generation failure.

---

### Update Test Case

```
PUT /api/v1/projects/{project_id}/testcases/{test_case_id}
```

**Request Body** (`TestCaseUpdate`): All fields optional.

```json
{
  "title": "Updated Title",
  "preconditions": ["precondition"],
  "steps": ["step 1", "step 2"],
  "expected_result": "Expected outcome",
  "priority": "high",
  "status": "active",
  "confidence": 0.98,
  "evaluation_status": "approved",
  "evaluation_reason": "string"
}
```

**Access**: a signed in user (`401`) with access to the project (`403`) and the `TestCases` `edit` permission (`403`). A test case from another project returns `404`.

**Response**: `200` — `TestCaseResponse`, `404` if not found.

---

### Delete Test Case

```
DELETE /api/v1/projects/{project_id}/testcases/{test_case_id}
```

**Access**: a signed in user (`401`) with access to the project (`403`) and the `TestCases` `delete` permission (`403`). A test case from another project returns `404`.

**Response**: `200` — `{"detail": "Test case deleted successfully"}`

---

### Generate Script

```
POST /api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script
```

**Request Body** (`GenerateScriptPayload`, optional): `{"framework": "Playwright"}`

Requires an approved test case. For Playwright projects the path depends on the case's saved data:

- If the test case already holds 20 distinct datasets, or the project has no `target_url`, the script is data driven, as described below.
- Otherwise the project's `target_url` is set and the case has no datasets, so the script is generated live against the target app, with one test (see "Live-verified script generation" in `docs/agents.md`).

The data-driven path:

1. If the test case does not yet hold 20 distinct datasets, `TestDataAgent` generates them first and stores them as `test_data_sets`, with a parallel `test_data_profiles` entry per dataset (`test_type`, `expected_outcome`, `targeted_validation_rule`).
2. `PlaywrightAgent` writes one template test from the case's real steps. The script holds the datasets once as an array (`const datasets = [{ id, type, data }, ...]`) and runs the template in a `for (const row of datasets)` loop. Each iteration is wrapped in `test.describe(row.id)`, so every dataset still reports as its own `TD-<id>-NN` test. Datasets whose expected outcome differs from the case's own (for example a valid row in a negative case) get a separate template and array (`acceptedDatasets` or `rejectedDatasets`), so each test asserts its real outcome.
3. When the project is marked as a safe test environment, each template is recorded on the live target app: the steps run in a headless browser with the group's first dataset, every action is verified on the page, and the expected result is checked live. This submits real forms. When recording fails or drops a data field, or the project is not marked safe, the template comes from one LLM call grounded on a read only inspection of the target URL (nothing is submitted). If the generated script fails the locator and TypeScript checks, it is regenerated once with that feedback.

The new script's `script_validation_status` is reset to `unverified`. Selenium and Cucumber keep single script generation.

**Response**: `200` — `TestCaseResponse`
**Error**: `400` if data or script generation fails, `404` if the test case, scenario, or requirement is not found, `500` on unexpected errors.

This call holds the request open until generation finishes, which can take longer than a client or proxy timeout. The frontend uses the background job routes below instead.

---

### Start Script Generation Job

```
POST /api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script/jobs
```

Runs the same generation as **Generate Script** in a background thread and returns at once. Requires a signed-in user. **Request Body**: same as Generate Script. If a job for this test case is already running, that job is returned instead of starting a second one.

**Response**: `202`:

```json
{"job_id": "uuid", "test_case_id": "uuid", "status": "running", "error": null, "error_status": null, "test_case": null}
```

### Get Script Generation Job

```
GET /api/v1/projects/{project_id}/script-generation-jobs/{job_id}
```

Poll every few seconds until `status` is no longer `running`. It then ends in one of two states:

- **`completed`**: `test_case` holds the `TestCaseResponse`.
- **`failed`**: `error` holds the message and `error_status` holds the status that Generate Script would have returned (`400`, `404` or `500`).

Jobs are kept in memory for one hour after they finish. A backend restart loses running jobs.

**Error**: `404` if the job is unknown, belongs to another project, or was lost in a restart.

---

### Update Playwright Script

```
PUT /api/v1/projects/{project_id}/testcases/{test_case_id}/script
```

**Access**: a signed in user (`401`) with access to the project (`403`) and the `TestCases` `edit` permission (`403`). A test case from another project returns `404`.

**Request Body** (`ScriptUpdatePayload`):

```json
{
  "script": "import { test, expect } from '@playwright/test';\n// ..."
}
```

**Response**: `200` — `TestCaseResponse`, `404` if not found.

Any injected object repository block in the submitted script is removed before saving, so stored scripts hold element keys only. Every `TestCaseResponse` (and the `/api/v1/playwright/workspace` responses) returns the script rendered with the current object repository locators. See [Object Repository](#object-repository).

---

## Object Repository

All routes sit under `/api/v1/projects/{project_id}/object-repository`, need a bearer token, and check the `ObjectRepository` permission module (`403` without it). Page and element IDs from another project return `404`. Feature guide: [features/object-repository.md](features/object-repository.md).

| Endpoint | Method | Permission | Body / query | Response | Errors |
|---|---|---|---|---|---|
| `/permissions` | GET | view | none | `{"actions": ["view", "create", ...]}` for the caller on this project | 403 |
| `/pages` | GET | view | `limit` (50, max 200), `offset` | `{"pages": [page + counts + score], "total"}` | 404 project |
| `/pages` | POST | create | `name`, `url_pattern` | `201` page | 409 duplicate, 422 invalid |
| `/pages/{page_id}` | PATCH | edit | `name`, `url_pattern` (optional) | `{"page", "scripts_rewritten"}` | 404, 409, 422 |
| `/pages/{page_id}` | DELETE | delete | none | `{"elements_deleted", "referencing_test_cases"}` | 404 |
| `/pages/{page_id}/elements` | GET | view | `health`, `limit` (100, max 500), `offset` | `{"elements", "total"}` | 404, 422 bad health |
| `/pages/{page_id}/elements` | POST | create | `name`, `element_type`, `locators` (1 to 5), `description` | `201` element | 409 duplicate, 422 invalid |
| `/elements/{element_id}` | PATCH | edit | `name`, `element_type`, `description`, `locators` (full ordered list) | `{"element", "scripts_rewritten"}` | 404, 409, 422 |
| `/elements/{element_id}` | DELETE | delete | none | `{"referencing_test_cases"}` | 404 |
| `/scan` | POST | create | none | `202` `{"scan_id"}` | 409 not a safe test environment, no target URL, or a scan already running |
| `/scan/{scan_id}` | GET | view | none | `{"scan_id", "status", "pages_scanned", "added", "updated", "marked_missing", "error"}` | 404 unknown, expired, or other project |
| `/heals` | GET | view | `status` (optional: `applied`, `suggested`, `accepted`, `rejected`, `reverted`) | list of heals, newest first, no pagination | 422 bad status |
| `/heals/{heal_id}/accept` | POST | edit | none | `{"heal", "element"}` | 404, 409 |
| `/heals/{heal_id}/reject` | POST | edit | none | heal | 404, 409 |
| `/heals/{heal_id}/revert` | POST | edit | none | `{"heal", "element"}` | 404, 409 |

**Rules**: names are snake_case (`^[a-z][a-z0-9_]*$`); `url_pattern` is a path glob that starts with `/` and has no `***` or `?` (`*` is one segment, `**` any number); `strategy` is one of `role`, `label`, `placeholder`, `text`, `test_id`, `css`, `xpath`; `element_type` is one of `button`, `link`, `input`, `select`, `checkbox`, `radio`, `textarea`, `nav`. Options: `role` allows `name` and `exact`, `label` and `text` allow `exact`, others `{}`.

Each listed page has `score: {"percent", "successes", "lookups"}`, the share of run lookups where any of its locators found the element: the locators' `success_count` against the elements' `failure_count`, over active elements. `percent` is `null` (one decimal otherwise) until a run uses one of the page's elements.

Any create or update sets `manually_edited = true`, `health = healthy`, and resets the failure counters. Locators whose `(strategy, value, options)` did not change keep their success and failure counts. A rename rewrites every quoted reference to the old key in the project's stored scripts in the same transaction. Every write records one audit entry (module `OBJECT_REPOSITORY`).

**Element** example:

```json
{
  "id": "uuid",
  "page_id": "uuid",
  "key": "login_page.login_button",
  "name": "login_button",
  "element_type": "button",
  "health": "healthy",
  "manually_edited": true,
  "last_used_at": null,
  "last_locator_index": null,
  "failure_count": 0,
  "consecutive_failed_runs": 0,
  "locators": [
    {"id": "uuid", "position": 0, "strategy": "role", "value": "button", "options": {"name": "Log in"}, "success_count": 0, "failure_count": 0, "last_succeeded_at": null}
  ],
  "latest_heal": null
}
```

### Heals (spec 0011)

A heal is one proposed or applied fix for an element's locators, written by self healing after a run. `latest_heal` on an element is its heal with the latest `created_at`, or `null`.

**Heal** example:

```json
{
  "id": "uuid",
  "element_id": "uuid",
  "key": "login_page.login_button",
  "status": "suggested",
  "method": "deterministic",
  "confidence": 0.72,
  "old_locators": [{"strategy": "test_id", "value": "login-btn", "options": {}}],
  "new_locators": [{"strategy": "test_id", "value": "sign-in-btn", "options": {}}],
  "reason": "test id and role match",
  "evidence_source": "harvest",
  "execution_id": "uuid",
  "test_case_id": "uuid",
  "created_at": "2026-11-25T10:00:00Z",
  "decided_by": null,
  "decided_at": null
}
```

* **Accept** applies a `suggested` heal: its locators go first, the old ones stay as backups (at most 5), the fingerprint moves to the candidate, `health` becomes `needs_review`, and `manually_edited` keeps its value. Nothing is rerun.
* **Reject** marks a `suggested` heal `rejected` and changes no locator.
* **Revert** puts back the locators and fingerprint an `applied` or `accepted` heal replaced, and marks it `reverted`.
* Accept, reject, and revert set `decided_by` and `decided_at`, and write one audit entry (entity `object_locator_heal`, action `heal_accepted`, `heal_rejected`, or `heal_reverted`, with the old and new main locator).
* `404`: an unknown heal, a heal from another project, or one whose element or page is deleted. `409 heal is <status>`: the heal is in the wrong status. `409 element changed, review again`: a tester edited the element (or another heal changed it) since the heal was computed or applied, so review it again.
* Each element keeps its pending suggestion, its latest applied or accepted heal, and its last 10 other decided heals.

### Script heals (spec 0011 amendment)

A script heal is one repair of a test case's Playwright script, made by self healing from the log analyser's RCA after an automation issue failure. `latest_script_heal` on every `TestCaseResponse` is the test case's newest script heal, or `null`. Scripts in a script heal hold object repository keys, like stored scripts.

Routes under `/api/v1/projects/{project_id}/testcases`:

| Path | Method | Permission | Inputs | Returns | Errors |
|---|---|---|---|---|---|
| `/script-heals` | GET | `TestCases` view | `status` (optional: `applied`, `suggested`, `accepted`, `rejected`, `reverted`) | list of script heals, newest first | 403, 404, 422 bad status |
| `/script-heals/{heal_id}/accept` | POST | `TestCases` edit | none | script heal | 403, 404, 409 |
| `/script-heals/{heal_id}/reject` | POST | `TestCases` edit | none | script heal | 403, 404, 409 |
| `/script-heals/{heal_id}/revert` | POST | `TestCases` edit | none | script heal | 403, 404, 409 |

**Script heal** fields: `id`, `test_case_id`, `status`, `old_script`, `new_script`, `rca_category`, `root_cause_summary`, `failing_step`, `reason`, `execution_id`, `created_at`, `decided_by`, `decided_at`.

* **Accept** saves `new_script` to a `suggested` heal's test case, sets `script_validation_status` to `unverified` with the diagnostic `Repaired by self healing`, and marks the heal `accepted`. Nothing is rerun.
* **Reject** marks a `suggested` heal `rejected` and changes no script.
* **Revert** puts back `old_script` for an `applied` or `accepted` heal and marks it `reverted`.
* Each decision sets `decided_by` and `decided_at`, and writes one audit entry (module `TESTCASE`, entity `test_case_script_heal`, action `script_heal_accepted`, `script_heal_rejected`, or `script_heal_reverted`). The script text is never written to the audit log.
* `404`: an unknown heal, or one from another project. `409 heal is <status>`: the heal is in the wrong status. `409 script changed, review again`: the stored script is no longer the one the heal starts from (accept) or put in place (revert).
* A test case has at most one `suggested` script heal; a newer one rejects the older with the reason `replaced by a newer suggestion`. Each test case keeps its suggestion, its latest applied or accepted heal, and its last 10 other decided heals.

### Execution webhook `logs`

`POST /api/v1/projects/{project_id}/executions/webhook` `completed` payloads carry an optional `logs` list holding only `[OBJREPO]` marker lines (at most 2000). `ExecutionService` turns them into element and locator health once per execution.

### Execution webhook `harvest`

Both `completed` payloads (passed and failed) carry an optional `harvest` list, at most `harvest_max_pages` items:

```json
"harvest": [
  {"url": "/inventory.html", "elements": [{"element_type": "button", "role": "button", "label": "Add backpack", "placeholder": "", "attributes": {"data-testid": "add-backpack", "id": "", "name": "", "tag": "button", "type": ""}, "locators": [{"strategy": "test_id", "value": "add-backpack", "options": {}, "match_count": 1}]}]}
]
```

`ExecutionService.apply_object_repository_harvest` keeps the first snapshot per path, redacts, and ingests each page without marking anything missing. A malformed snapshot is skipped with a warning and never fails the webhook. The Python runner sends the same snapshots through `harvest.json` in the run directory instead.

### `GET /api/v1/object-repository/harvest-settings`

No authentication (global settings, no secrets). The execution engine calls it at run start, before its credentials fetch (which needs the workspace secret).

```json
{"enabled": true, "max_pages": 20, "budget_ms": 5000}
```

### `GET /api/v1/accessibility/scan-settings?project_id=...`

Header secret `WORKSPACE_WEBHOOK_SECRET` (spec 0011). The execution engine reads it once at the top of each run, before the `started` webhook. `enabled` is the engine's final scan decision: the project's `a11y_enabled` and the global `accessibility.enabled`. An unknown project is `404`. Any failure (timeout of 2 seconds, non 200, bad JSON) means the engine does not scan that run.

```json
{"enabled": true, "gate_mode": "warn", "block_severity": "serious", "wcag_tags": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"], "max_pages": 10, "page_budget_ms": 30000, "max_element_screenshots": 10, "max_nodes_per_rule": 50, "webhook_payload_max_bytes": 1000000}
```

### Execution webhook `batch_id` and `test_cycle_id`

Spec 0012: a `completed` payload from a workspace run of several selected cases carries `batch_id`, one UUID shared by every case of that run. A single case run sends none. `test_cycle_id` is sent only when a real cycle was selected. `finalize_execution` saves a `batch_id` that isn't a UUID as `null`, keeps the `batch_id` saved first when the webhook repeats, and saves a `test_cycle_id` that matches no cycle of this project as `null`. Each drop logs a warning naming the execution id. Neither field ever fails the webhook.

### Execution webhook `a11y`, `a11y_settings`, and `a11y_settings_error`

Both `completed` payloads carry the run's accessibility fields (spec 0011):

- `a11y_settings`: `{enabled, gate_mode, block_severity}`, exactly as the engine read them from `scan-settings`. Sent whenever that read succeeded, even when `enabled` is `false`.
- `a11y_settings_error`: the reason the read failed (`timeout`, `http_<status>`, `bad_json`, `no_project`), sent instead of `a11y_settings`. Only logged; it never changes the gate.
- `a11y`: the raw axe results of the pages the run visited, only when the engine scanned. At most 50 nodes per rule per page and `webhook_payload_max_bytes` in total: snippets are dropped first, then whole page results, and the payload is marked `truncated: true`.

`finalize_execution` builds the run's `project_config_snapshot` (a saved snapshot wins, so a repeated webhook keeps its first gate). Otherwise it captures the project now, and the echoed `gate_mode` and `block_severity` replace the project's values only when they are valid (`off`, `warn`, `block`; `critical`, `serious`). A scanning run with no `a11y` field gets `gate_status: NOT_SCANNED` with `gate_reason: scan_error`, never a pass. Neither field can fail the webhook or change the run's status. The webhook saves the report and its gate with axe's own text before it responds. The AI explanation and the accessibility bug drafts (which quote it) are added after the response, so the engine never waits on the LLM. Batch runs scan through the Python runner and read `a11y.json` from the run folder instead.

---

## Executions

The two execution delete routes, `DELETE /api/v1/projects/{project_id}/executions/{execution_id}` and `DELETE /api/v1/projects/{project_id}/executions/failed`, need a signed in user (`401`) with access to the project (`403`) and the `Executions` `delete` permission (`403`). An execution from another project returns `404`.

### Execute Test Case

```
POST /api/v1/projects/{project_id}/executions
```

**Request Body**:

```json
{
  "test_case_ids": ["uuid"],
  "execution_id": "uuid (optional)"
}
```

Runs ExecutionAgent → ExecutionAnalysisAgent → BugReportingAgent → ReportAgent pipeline.

**Response**: `200` — `ExecutionResult`

```json
{
  "id": "uuid",
  "test_case_id": "uuid",
  "status": "passed|failed|error|skipped",
  "started_at": "datetime",
  "executed_at": "datetime",
  "duration_seconds": 12.5,
  "error_message": "string|null",
  "screenshot_path": "string|null",
  "video_path": "string|null",
  "trace_path": "string|null",
  "timeline": "array|null",
  "screenshots": "array|null",
  "failure_category": "Product Bug|Test Environment Issue|Flaky Test / Automation Issue|None|null",
  "root_cause_summary": "string|null",
  "trace_analysis": "string|null",
  "screenshot_findings": "string|null",
  "suggest_retry": "boolean|null",
  "retest_pending_candidate": false,
  "failing_step": "string|null",
  "evidence": ["log line quoted by the log analyser"],
  "confidence": "number 0-1|null",
  "flaky_score": "number 0-1|null",
  "frontend_error_detected": "boolean|null",
  "frontend_error_details": "string|null",
  "run_history_summary": {"runs": 5, "passes": 3, "fails": 2, "flips": 4, "same_error_count": 1, "outcomes": ["P", "F", "P", "F", "P"], "flaky_score": 0.9},
  "jira_bug_id": "string|null",
  "jira_bug_url": "string|null",
  "test_cycle_id": "uuid|null (a real test cycle of this project only)",
  "batch_id": "uuid|null (spec 0012: shared by every run of one batch)",
  "executed_by": "uuid|null",
  "project_config_snapshot": {
    "project_id": "uuid",
    "target_url": "normalized project URL",
    "framework": "playwright",
    "credential_reference": "uuid"
  }
}
```

### Project configuration snapshot (spec 0001)

`POST /api/v1/projects/{project_id}/testcases/{test_case_id}/execute` and `POST /api/v1/projects/{project_id}/batches/execute` check the project's readiness before anything is queued. A project without a valid `target_url` or `framework` gets `422` with the shared configuration error, and no run starts:

```json
{
  "code": "PROJECT_NOT_READY",
  "message": "Project configuration is incomplete.",
  "missing_fields": ["target_url"],
  "invalid_fields": {},
  "action": {"label": "Open Project Settings", "route": "#/settings?tab=project"}
}
```

A ready project's configuration is captured at that moment as `project_config_snapshot`. The runner sets `TARGET_URL` from it (never from the server's own environment), every result of the run stores it, and it is never changed after it is saved. It holds no secret: credentials are read from the vault by `credential_reference`. Runs saved before this change have `null`.

---

## Accessibility

Accessibility reports written by the Accessibility Agent (spec 0011, guide: [features/accessibility-agent.md](features/accessibility-agent.md)). Read routes need `Executions.view` and starting a scan needs `Executions.edit`, both with access to the project. A report or run from another project returns `404`. An automated scan does not certify WCAG conformance.

A report has `source` (`execution` or `scan`), `status` (`queued`, `running`, `succeeded`, `failed`), `error` (`scan_error`, `login_failed`, `timeout`, or `null`), `login_status` (`not_requested`, `succeeded`, `failed`), `pages[]` (`{url, title, scanned_at, requires_login, error}`, where `error` is `budget`, `csp_blocked`, `requires_login`, `navigation_failed`, `axe_error`, or `null`), `findings[]`, `counts` (`{critical, serious, moderate, minor, needs_review}`), `gate_status` (`PASS`, `WARN`, `BLOCKED`, `NOT_SCANNED`), `gate_reason` (`disabled`, `scan_error`, or `null`), and `gate_mode` (the mode the run used).

### Run report

```
GET /api/v1/projects/{project_id}/executions/{execution_id}/accessibility
```

The run's full report with findings. A run with no report returns `{"gate_status": "NOT_SCANNED", "gate_reason": "no_report"}` (or `disabled` / `scan_error` when the report says so).

### Release gate (for CI)

```
GET /api/v1/projects/{project_id}/accessibility/gate?execution_id=...
GET /api/v1/projects/{project_id}/accessibility/gate?batch_id=...
```

Exactly one of `execution_id` or `batch_id` (`422` otherwise). The batch id is the runs' `batch_id` (spec 0012). A `batch_id` that isn't a UUID is `404`. So is one that matches no run or report of this project and has no backend batch log for this project (a workspace batch with no saved run yet, a batch from before a restart, or another project's batch). Accepts a user session with `Executions.view`, or the header `X-Webhook-Secret`.

```json
{"gate_status": "BLOCKED", "gate_reason": null, "gate_mode": "block", "runs": [{"execution_id": "uuid", "gate_status": "BLOCKED"}], "in_progress": false}
```

`in_progress` (spec 0012) is `true` while this backend is still running this project's batch (its event log exists, was started by this project, and its last event isn't `Completed` or `Error`). It is `false` for an `execution_id` gate, for workspace batches, and after a backend restart.

A backend batch with no saved run or report yet still answers `200`, from its event log:

| Log state | `gate_status` | `gate_reason` | `gate_mode` | `runs` | `in_progress` |
|---|---|---|---|---|---|
| still running | `NOT_SCANNED` | `batch_running` | `null` | `[]` | `true` |
| ended (`Completed` or `Error`) | `NOT_SCANNED` | `batch_no_runs` | `null` | `[]` | `false` |

Once a run or report is saved, the normal batch gate applies and `in_progress` stays `true` until the last event. So CI checks `in_progress` before `gate_status`: poll while it is `true`, then read the gate. `batch_no_runs` means the batch ended before any run was saved, which usually means it failed to start. A workspace batch has no backend log, so an early `404` there can mean "not saved yet".

A batch returns its worst run (`BLOCKED`, then `WARN`, then `NOT_SCANNED`, then `PASS`). A run with no report counts as `NOT_SCANNED`, and `gate_mode` is `mixed` when the runs used different modes. The gate never changes a run's functional `status`.

### Scan pages

```
GET /api/v1/projects/{project_id}/accessibility/scan-pages
```

The checklist for a standalone scan: the project target URL, literal object repository page patterns, and URLs named in test cases, one per path. Wildcard patterns are listed with `skipped_reason: "wildcard"`.

```json
{"pages": [{"url": "https://app.example.com/login", "source": "target_url", "skipped_reason": null}, {"url": "/orders/*", "source": "object_repository", "skipped_reason": "wildcard"}], "max_pages": 10}
```

### Start a standalone scan

```
POST /api/v1/projects/{project_id}/accessibility/scans
```

```json
{"paths": ["/", "/account"], "include_login": false}
```

Returns `202` with `{"report_id": "uuid", "status": "queued"}`. The scan runs in the background: `queued`, then `running`, then `succeeded` or `failed`. Poll the report routes below.

- When the global `accessibility.enabled` (`A11Y_ENABLED`) is off, every start is `409` (`Accessibility scanning is turned off on this server`). The project's `a11y_enabled` covers test runs only and does not stop a scan.
- `paths` may be absolute URLs or paths, and must be on the project target URL's origin. An empty list scans the target URL only. Off origin, or more than `accessibility.max_pages` (default 10) pages, is `422`.
- Only one scan per project can be queued or running (`409`).
- `include_login: true` needs the project marked as a safe test environment (`409`), because it submits the stored credentials. The scan first opens every page with no session, and a page that redirects to a visible password field is marked `requires_login`. It then fills the login form on the target URL, and the login succeeds when the URL path changes, no password field is visible, and no `[role=alert]` shows. On success the `requires_login` pages are scanned signed in; on failure the report gets `login_status: failed`, `error: login_failed`, and those pages keep `error: requires_login`. Multi step and single sign on logins are not supported.
- A project that is not ready (no valid target URL) is `409`. An unknown project is `404`.
- A scan still queued or running after `accessibility.scan_stale_after_seconds` (default 900) is marked `failed` with `error: timeout`. This is checked on every read of the report routes and when the server starts.
- Serious findings become accessibility bug drafts the same way run findings do (see Bug Reports).

### List reports

```
GET /api/v1/projects/{project_id}/accessibility/reports?source=scan&limit=20&offset=0
```

`source` is optional (`execution` or `scan`), `limit` is 1 to 100 (default 20). Newest first, without findings:

```json
{"items": [{"id": "uuid", "source": "scan", "status": "succeeded", "gate_status": "WARN", "counts": {"critical": 1, "serious": 0, "moderate": 0, "minor": 0, "needs_review": 0}, "pages": []}], "total": 1, "limit": 20, "offset": 0}
```

### Get one report

```
GET /api/v1/projects/{project_id}/accessibility/reports/{report_id}
```

The full report with findings.

### Reports and JUnit

HTML and PDF execution reports (legacy and professional engines) include an Accessibility section with the gate, counts, and a findings table, or the not scanned state with its reason. JUnit files carry a `testsuite name="accessibility"`: one failing `testcase` per rule with a violation, a single passing `testcase` when there are none, and a single skipped `testcase` when the run was not scanned. The professional engine's JUnit root is now `testsuites`.

---

## Bug Reports

Drafts written by the Bug Reporting Agent for Product Bug failures, reviewed before they are filed in Jira. GET routes need `Executions.view`; the others need `Executions.edit`. Every route is scoped to the project in the path. A report from another project returns `404`.

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/v1/projects/{project_id}/bug-reports?status=` | Newest first. `status` is one of `draft`, `submit_failed`, `submitted`, `linked_duplicate`, `resolved`, `dismissed`. |
| GET | `/api/v1/projects/{project_id}/bug-reports/{report_id}` | One report. |
| POST | `/api/v1/projects/{project_id}/executions/{execution_id}/bug-report` | Drafts a report for a failed or errored run, whatever its triage category, and saves `bug_report_id` on the run. It returns the run's existing report if it has one. A passed run returns `409`. |
| PATCH | `/api/v1/projects/{project_id}/bug-reports/{report_id}` | Edits `title`, `summary`, `steps_to_reproduce`, `expected_result`, `actual_result`, `severity`, `priority`. Only a `draft` or `submit_failed` report can be edited (else `409`). Text is redacted against project credentials. |
| POST | `/api/v1/projects/{project_id}/bug-reports/{report_id}/submit` | Searches Jira for a duplicate, then comments on it (`linked_duplicate`) or creates a Bug (`submitted`). A Jira error returns `200` with `status: submit_failed` and `submit_error`. |
| POST | `/api/v1/projects/{project_id}/bug-reports/{report_id}/dismiss` | Body `{"reason": "..."}`. Draft or `submit_failed` only. |

`BugReport` fields:
- **Links:** `id`, `project_id`, `execution_id`, `test_case_id`, `accessibility_report_id`, `scenario_id`, `requirement_id`. `execution_id` and `test_case_id` are `null` on a bug from a standalone accessibility scan; every report has an `execution_id` or an `accessibility_report_id`.
- **Content:** `title`, `summary`, `steps_to_reproduce`, `expected_result`, `actual_result`, `severity`, `priority`, `environment`
- **From triage:** `failing_step`, `root_cause`, `evidence`, `confidence`, `flaky_score`, `failure_category`
- **Accessibility:** `a11y_rule_id`, `wcag_criteria` (set when `failure_category` is `Accessibility Issue`)
- **Artifacts and dedupe:** `attachments`, `error_signature`, `occurrences` (`[{execution_id, accessibility_report_id, executed_at}]`)
- **Status and Jira:** `status`, `jira_key`, `jira_url`, `duplicate_of_jira_key`, `submit_error`, `dismiss_reason`
- **Audit:** `created_by`, `submitted_by`, `created_at`, `updated_at`

The execution detail (`GET .../executions/{id}`) includes `bug_report_id`.

Project settings (`PUT /api/v1/projects/{project_id}`) accept `bug_auto_submit` (boolean, default `false`) and `bug_auto_submit_min_confidence` (0 to 1, default `0.85`; anything else is `422`). Both are returned on the project. With auto-submit on, a draft whose triage confidence reaches the threshold is submitted as soon as it is drafted.

**Accessibility bugs (spec 0011).** After a run is scanned, each violation at or above the project's `a11y_block_severity` becomes a draft with `failure_category: "Accessibility Issue"` (critical maps to severity `critical`, serious to `major`). One open bug covers one finding signature per project, across runs and scans: a repeat adds an occurrence (and a Jira comment once filed). A `dismissed` bug ignores repeats, and a `resolved` bug gets a new draft. `needs_review` findings never create bugs. Accessibility bugs follow `bug_auto_submit` (there is no confidence threshold), carry the Jira labels `accessibility`, `a11y_<rule_id>`, and `wcag_<criterion>`, attach the element screenshot when one was captured, and never change the run's `bug_report_id` or `jira_bug_id`. The run's own `bug-report` route only returns its functional bug.

Element screenshots: `GET /api/v1/projects/{project_id}/accessibility/reports/{report_id}/screenshots/{name}` (`Executions.view` and project access) returns the PNG a finding's `screenshot_path` names. A name with a path in it, a name no finding of that report records, a missing file, or a report from another project is `404`. The scan captures critical and serious violation nodes only, up to `accessibility.max_element_screenshots` (default 10) per run.

Project settings also accept the Accessibility Agent settings (spec 0011): `a11y_enabled` (boolean, default `true`), `a11y_gate_mode` (`off`, `warn`, or `block`, default `warn`), and `a11y_block_severity` (`critical` or `serious`, default `serious`). Any other value is `422`, and a `null` leaves the value unchanged. All three are returned on the project and copied into each run's `project_config_snapshot` when it starts, so the run's accessibility gate always uses the settings it started with.

### Sync a failed run as a Jira bug

`POST /api/v1/projects/{project_id}/executions/{execution_id}/sync-bug` (`Executions.edit`) drafts a bug report if the run has none, then submits it straight away. Returns `{"status": "success", "logs": [...]}`.

### Jira webhook

`POST /api/v1/webhooks/jira` (header secret `JIRA_WEBHOOK_SECRET`). When the issue moves to a status in `jira.resolved_statuses`, linked test cases become `retest_pending` and matching bug reports become `resolved`. The response includes `updated_count` and `resolved_bug_reports`.

## Dashboard

### Get Dashboard Metrics

```
GET /api/v1/projects/{project_id}/dashboard-metrics?scope=project|release|cycle&id=optional-uuid
```

**Response**: `200` — `DashboardMetricsResponse`

```json
{
  "totalReqs": 10,
  "totalScenarios": 25,
  "totalTestCases": 60,
  "totalExecutions": 120,
  "passRatio": "85%",
  "coveragePct": "75%",
  "uiCount": 40,
  "apiCount": 15,
  "manualCount": 5,
  "passedCount": 45,
  "failedCount": 15,
  "yetToExecuteCount": 0,
  "approvedCount": 20,
  "rejectedCount": 3,
  "pendingCount": 2,
  "trendLabels": ["Jul 10", "Jul 11", ...],
  "trendPassed": [5, 8, ...],
  "trendFailed": [1, 2, ...],
  "domainLabels": ["authentication", "payment", ...],
  "domainCountsData": [5, 3, ...],
  "priorityHighCount": 20,
  "priorityMediumCount": 30,
  "priorityLowCount": 10,
  "defectOpenCount": 5,
  "defectResolvedCount": 3,
  "defectRetestCount": 2,
  "defectClosedCount": 10,
  "recentExecutions": [...],
  "isEmpty": false
}
```

---

## Token Usage

Real LLM token usage and cost recorded for every agent call. See [Token Utilization](features/token-utilization.md). All routes need the `TokenUsage` permission: `view` for reads, `edit` to change settings.

**Common filters** (query string, all optional): `from`, `to` (ISO 8601; a value without a timezone is read as UTC; `to` is exclusive), `project_id`, `agent`, `model`, `role`, `provider`, `status` (`success` | `error` | `rate_limited`), `operation`.

### Summary

```
GET /api/v1/token-usage/summary
```

```json
{
  "calls": 42, "prompt_tokens": 81000, "completion_tokens": 23000, "total_tokens": 104000,
  "cost_usd": 0.0412, "avg_tokens_per_call": 2476.2, "avg_cost_per_call": 0.00098,
  "avg_latency_ms": 3120, "errors": 2, "error_rate": 0.0476, "retries": 3, "unpriced_calls": 0,
  "previous": { "...": "same totals for the previous period of equal length, or null" },
  "deltas": { "cost_usd": 12.5, "total_tokens": -4.0, "calls": null },
  "unpriced_models": [],
  "first_call_at": "2026-11-01T09:00:00Z", "last_call_at": "2026-11-12T10:00:00Z",
  "budget": {
    "monthly_budget_usd": 25.0, "alert_threshold_pct": 80.0, "month_to_date_usd": 4.1,
    "projected_month_end_usd": 10.2, "used_pct": 16.4, "projected_pct": 40.8, "state": "ok"
  }
}
```

`previous` and `deltas` are only filled when both `from` and `to` are given. `budget.state` is `none`, `ok`, `warning` (above the threshold, or projected over budget) or `exceeded`.

### Time series

```
GET /api/v1/token-usage/timeseries?bucket=hour|day|week&split_by=agent|model|role|provider|project_id|operation|status
```

Returns `labels` (bucket starts, UTC) and `series`. Each series item is `{key, tokens[], prompt_tokens[], completion_tokens[], cost_usd[], calls[]}`. With no `split_by` there is one series, `All`. A split keeps the 8 largest series by cost and folds the rest into `Other`.

### Breakdown

```
GET /api/v1/token-usage/breakdown?group_by=agent|model|role|provider|project_id|operation|status
```

Returns `{group_by, rows: [...]}`, sorted by cost. Each row has these fields:
- The group: `key`, plus `models` and `roles`.
- Volume: `calls`, `prompt_tokens`, `completion_tokens`, `total_tokens`.
- Cost: `cost_usd`, `cost_share_pct`, `token_share_pct`.
- Averages: `avg_tokens_per_call`, `avg_cost_per_call`, `avg_latency_ms`.
- Failures: `errors`, `error_rate`, `retries`, `unpriced_calls`.
- `stage`, only when grouping by agent.

`400` for an unknown `group_by`.

### Call log

```
GET /api/v1/token-usage/calls?page=1&page_size=25&sort=-created_at
```

`sort` is one of `created_at`, `cost_usd`, `total_tokens`, `latency_ms`, `agent` or `model`. Prefix it with `-` for descending order. `page_size` is capped at 200. Returns `{page, page_size, total, items}`, where each item is a full usage record plus `stage`.

### Facets

```
GET /api/v1/token-usage/facets
```

Distinct `agent`, `model`, `role`, `provider`, `status`, `operation` and `project_id` values, for filter dropdowns.

### Export CSV

```
GET /api/v1/token-usage/export.csv
```

`text/csv` attachment of the filtered records, one row per attempt.

### Pricing

```
GET /api/v1/token-usage/pricing
```

The effective price of every configured model and every model seen in usage, as a list of `{provider, model, model_string, roles, input_per_1m, output_per_1m, source}`. `source` is `override`, `litellm` or `none`.

### Settings

```
GET /api/v1/token-usage/settings
PUT /api/v1/token-usage/settings
```

```json
{
  "monthly_budget_usd": 25.0,
  "alert_threshold_pct": 80,
  "pricing_overrides": { "meta/llama-3.1-70b-instruct": { "input_per_1m": 0.35, "output_per_1m": 0.4 } }
}
```

`PUT` replaces all three fields and returns the saved settings. Values are validated: the budget must be 0 or more, and the threshold must be between 0 and 100.

---

## Releases & Test Cycles

### List Releases

```
GET /api/v1/projects/{project_id}/releases
```

### Create Release

```
POST /api/v1/projects/{project_id}/releases
```

```json
{
  "name": "Release 1.0",
  "description": "First release",
  "status": "Active",
  "start_date": "2026-01-01T00:00:00Z",
  "end_date": "2026-03-31T00:00:00Z"
}
```

### List Test Cycles

```
GET /api/v1/releases/{release_id}/cycles
```

### Create Test Cycle

```
POST /api/v1/releases/{release_id}/cycles
```

```json
{
  "name": "Sprint 1",
  "description": "First sprint cycle",
  "status": "Active"
}
```

### List Project Cycles

```
GET /api/v1/projects/{project_id}/cycles
```

Returns cycles across all releases for the project.

---

## Credentials

### Get Project Credentials

```
GET /api/v1/projects/{project_id}/credentials
```

For the Playwright workspace only: needs the `X-Webhook-Secret` header matching `WORKSPACE_WEBHOOK_SECRET` (`401` when missing or wrong, `503` when the variable is empty). Returns this project's own decrypted username and password from the vault. It never falls back to another project's credentials.

**Response**: `200`, or `404` when the project has no stored credentials.

```json
{
  "username": "qa-user",
  "password": "********"
}
```

---

## Chat Assistant

Spec 0005. All routes need a bearer token and access to the project (Superadmin or Admin, the project owner, or a project assignment): an unknown project returns `404`, no access returns `403`. A thread that belongs to another user, or to another project, returns `404`. When `CHAT_ENABLED` is false every chat route returns `404`.

### Send Chat Message

```
POST /api/v1/projects/{project_id}/chat/messages
```

**Request Body**:

```json
{
  "thread_id": "uuid or omitted",
  "content": "Why did this fail?",
  "view_context": {"view": "playwright", "item_type": "test_case", "item_id": "uuid"}
}
```

`content` is 1 to 4000 characters and not blank. Without `thread_id` a new thread is created, titled with the first 80 characters of the message (whitespace collapsed). `view_context` is untrusted: an unknown view or item type, or an item that is not in this project, is dropped without an error. The user message is saved before the model is called.

**Response**: `200` with `text/event-stream`. Each event is an `event:` name and a JSON `data:` line:

| Event | Data | When |
|---|---|---|
| `thread` | `{thread_id, title}` | first, only when a thread was just created |
| `message_start` | `{user_message_id, assistant_message_id}` | the reply row exists (empty) |
| `token` | `{text}` | each piece of the reply |
| `tool` | `{name, state}` | a read tool starts (`state: "start"`) or ends (`"end"`); the panel shows "Looking up test cases…" |
| `action` | `{message_id, action}` | the assistant proposed an action (shape under *Chat Actions*); the turn ends after it |
| `done` | `{assistant_message_id}` | the full reply is saved |
| `error` | `{code, message, retryable}` | the reply stopped; the partial text is saved with `interrupted: true` |

The assistant answers over the project's data with read tools: `get_project_overview`, `list_requirements`, `get_requirement`, `list_scenarios`, `list_test_cases`, `get_test_case`, `list_executions`, `get_execution`, `get_dashboard_metrics`, `list_object_repository_pages`, `list_bug_reports`, and `get_bug_report`. Every ID a tool is given must belong to the path's project, otherwise the tool answers "not found in this project". No tool returns credentials, and test data values under keys that look like secrets (`password`, `token`, `secret`, `key`) are sent as `"[redacted]"`. Each call is saved as a hidden `tool` row (never returned by the messages route), with its result cut to `CHAT_TOOL_RESULT_CHARS` (default 4000) by dropping list rows from the end (`"truncated": true`, `"total": N`). A message makes at most `CHAT_MAX_TOOL_STEPS` (default 6) tool calls; past that, the reply ends with a note that the answer may be incomplete.

`code` is `llm_busy` (the chat pool's max wait ran out; message "The assistant is busy, try again in a moment"), `llm_error`, or `internal`. A client that disconnects mid reply also leaves the partial text saved as interrupted.

**Errors**: `404` thread not found, `422` invalid content.

### List Chat Threads

```
GET /api/v1/projects/{project_id}/chat/threads?cursor=<opaque>&limit=20
```

The caller's threads on this project, most recent activity first. `limit` is 1 to 50 (default 20).

**Response**: `200` — `{"items": [{"id": "uuid", "title": "...", "last_activity_at": "ISO 8601"}], "next_cursor": "opaque or null"}`

**Errors**: `422` invalid cursor.

### List Chat Messages

```
GET /api/v1/projects/{project_id}/chat/threads/{thread_id}/messages?before_seq=<int>&limit=50
```

The visible messages of a thread (user and assistant rows; tool rows are never returned), in `seq` order. Without `before_seq` it returns the newest `limit` messages (1 to 100, default 50); with it, the newest `limit` messages below that `seq`. `has_more` says whether older messages remain.

**Response**: `200` — `{"items": [{"id": "uuid", "seq": 1, "role": "user", "content": "...", "interrupted": false, "action": null, "created_at": "ISO 8601"}], "has_more": false}`

### Retry a Chat Reply

```
POST /api/v1/projects/{project_id}/chat/threads/{thread_id}/retry
```

Retries the last user message when its assistant reply was interrupted or ended with an unconfirmed action proposal. A completed assistant reply cannot be retried. The response is the same `text/event-stream` format as Send Chat Message; retry removes the old partial reply and its hidden tool rows while retaining the user message. The request counts toward the shared per-user chat rate limit.

**Errors**: `404` thread not found, `409` no retryable turn or a reply is already in progress, `429` rate limit exceeded.

### Help Without a Project

```
POST /api/v1/chat/help
```

Body: `{"content": "How do I import a requirement?", "history": [{"role": "user | assistant", "content": "..."}]}`. Content has the same 1-to-4000-character validation as a project message. Help answers use only the built-in app guide; history is limited to the last 10 user/assistant messages and 4000 characters per entry. Help turns are not persisted and share the per-user rate limit with project messages and retries. The response is `text/event-stream` with `message_start`, `token`, `done`, or `error` events; it does not include a thread event or project tools.

### Delete a Chat Thread

```
DELETE /api/v1/projects/{project_id}/chat/threads/{thread_id}
```

Deletes the caller's thread and its messages. Returns `204`; a thread owned by another user or project returns `404`, and an active reply returns `409`. Idle threads are also purged at startup and when listing threads, according to the configured chat retention period.

### Chat Actions

The assistant can propose a change, but never makes one by itself. With a project selected, the model is offered one `propose_<type>` tool per action type the user holds the permission for on this project. A valid proposal is saved on the assistant message as an `action` with status `proposed` and shown as a card with Run and Cancel; at most one action per assistant message. Sending a new message in the thread moves every earlier `proposed` action to `expired`.

Supported action types:

| Type | Params | Permission | Danger | Result link |
|---|---|---|---|---|
| `generate_scenarios` | `requirement_id`, `count` 1–10 (default 3), `mode` `append` or `replace` | Scenarios / create | `change`; `delete` for `replace` when existing scenarios will be removed | `scenarios` |
| `generate_test_cases` | `requirement_id` | TestCases / create | `change` | `testcases` |
| `generate_script` | `test_case_id`, optional `framework` | Playwright / create | `change` | `playwright` |
| `run_test_case` | `test_case_id` | Executions / create | `change` | `executions` |
| `run_batch` | `test_case_ids` (1–50, each with a script) | Executions / create | `change` | `executions` |
| `create_requirement` | `title`, `description`, optional `priority`, `business_domain` | Requirements / create | `change` | `requirements` |
| `update_requirement` | `requirement_id`, non-empty `changes` | Requirements / edit | `change` | `requirements` |
| `delete_requirement` | `requirement_id` | Requirements / delete | `delete` | `requirements` |
| `update_scenario` | `scenario_id`, non-empty `changes` | Scenarios / edit | `change` | `scenarios` |
| `duplicate_scenario` | `scenario_id` | Scenarios / create | `change` | `scenarios` |
| `delete_scenario` | `scenario_id` | Scenarios / delete | `delete` | `scenarios` |
| `update_test_case` | `test_case_id`, non-empty `changes` | TestCases / edit | `change` | `testcases` |
| `review_scenario` | `scenario_id`, `decision` `approved` or `rejected`; rejecting deletes the scenario's test cases | Scenarios / edit | `change` | `scenarios` |
| `review_test_case` | `test_case_id`, `decision` `approved` or `rejected` | TestCases / edit | `change` | `testcases` |
| `generate_test_data` | `test_case_id` (Playwright project and test case only) | TestCases / edit | `change` | `testcases` |
| `delete_test_case` | `test_case_id` | TestCases / delete | `delete` | `testcases` |
| `delete_execution` | `execution_id` | Executions / delete | `delete` | `executions` |
| `delete_failed_executions` | server-selected currently failed execution IDs | Executions / delete | `delete` | `executions` |
| `report_bug` | `execution_id` of a failed or errored run; drafts a bug report, nothing goes to Jira | Executions / edit | `change` | `executions`, `bugs` |
| `submit_bug_report` | `bug_report_id` of a `draft` or `submit_failed` report; searches Jira for a duplicate, then comments or creates a Bug | Executions / edit | `change` | `bugs`, `executions` |

`replace` generates first and deletes the old scenarios only after generation succeeds, so a failed run leaves them in place. Delete actions require typing exactly `DELETE`. Edit cards show the previous and proposed values; requirement deletion also lists its dependent scenarios and test cases. The batch action uses the existing sequential queue manager, shared browser state, recovery, execution records, artifacts, and reports.

**Action shape**:

```json
{
  "action_id": "uuid",
  "type": "generate_scenarios",
  "params": {"requirement_id": "uuid", "count": 3, "mode": "append"},
  "summary": "text written by the model, shown as secondary text",
  "targets": [{"type": "requirement", "id": "uuid", "name": "Login works"}],
  "danger": "none | change | delete",
  "permission": {"module": "Scenarios", "action": "create"},
  "affects": ["scenarios"],
  "status": "proposed | queued | running | succeeded | failed | cancelled | expired",
  "job_ref": {"kind": "inline", "id": null},
  "result": {"summary": "Created 3 scenarios", "link_view": "scenarios", "link_id": "uuid"},
  "error": null,
  "confirmed_by": "user uuid or null",
  "updated_at": "ISO 8601"
}
```

```
POST /api/v1/projects/{project_id}/chat/messages/{message_id}/action/confirm
```

Body: `{"confirm_text": "DELETE"}` (needed only when `danger` is `delete`; anything else in the body is ignored). Confirm runs the params saved with the proposal. Checks, in order: thread owner (`404`), the action's permission (`403`, "You no longer have permission"), `confirm_text` exactly `DELETE` for deletes (`422`), every target still in the project (`422`, "This item changed or no longer exists, ask again"), then an atomic move out of `proposed` (`409` when it was expired, cancelled, or already confirmed). Returns the updated action, usually `running`.

```
POST /api/v1/projects/{project_id}/chat/messages/{message_id}/action/cancel
```

Moves a `proposed` action to `cancelled`. Returns the action. **Errors**: `404`, `409` not `proposed`.

```
GET /api/v1/projects/{project_id}/chat/messages/{message_id}/action
```

The action with its current status. The panel polls it every 3 seconds while the status is `queued` or `running`, and reloads the affected view once it reaches `succeeded`. **Errors**: `404`.

Every confirm, cancel, and final result writes an audit entry (`CHAT_ACTION_CONFIRMED`, `CHAT_ACTION_CANCELLED`, `CHAT_ACTION_SUCCEEDED`, `CHAT_ACTION_FAILED`) with the user, project, action type, target IDs, outcome, and `source: "chat"`. At startup, an action left `queued` or `running` by the previous process becomes `failed` with "Interrupted by a server restart", and a reply cut off by the restart is marked interrupted.

---

## Settings

### Read Settings

```
GET /api/v1/settings
```

Returns all configuration sections (LLM, Workflow, Browser, RAG, Generation, Evaluation, Playwright, JIRA).

**Response**: `200`

```json
{
  "llm": { "provider": "ollama", "model": "llama3.1", ... },
  "llm_roles": {
    "generation": { "provider": "gemini", "models": ["model-a", "model-b"], "max_concurrent": 2,
                    "min_interval_seconds": 4, "cooldown_seconds": 60, "max_wait_seconds": 180 },
    "analysis": { "provider": "nvidia", "models": ["model-n"], "max_concurrent": 2,
                  "min_interval_seconds": 0, "cooldown_seconds": 60, "max_wait_seconds": 180 },
    "chat": { "provider": "nvidia", "models": ["model-n"], "max_concurrent": 4,
              "min_interval_seconds": 0, "cooldown_seconds": 60, "max_wait_seconds": 30 },
    "retrieval": { "provider": "nvidia", "models": ["model-n"], "max_concurrent": 2,
                   "min_interval_seconds": 0, "cooldown_seconds": 30, "max_wait_seconds": 20 }
  },
  "chat_enabled": true,
  "workflow": { "mode": "sequential", "human_review_enabled": true, ... },
  ...
}
```

`llm_roles` is read only and comes from the `GENERATION_LLM_*`, `ANALYSIS_LLM_*`, `CHAT_LLM_*`, and `RETRIEVAL_LLM_*` variables in `.env` (specs 0003, 0005, and 0006). Any unset `CHAT_LLM_*` value falls back to the resolved analysis role; the chat pool has its own limits (`CHAT_LLM_MAX_CONCURRENT` 4, `CHAT_LLM_COOLDOWN_SECONDS` 60, `CHAT_LLM_MAX_WAIT_SECONDS` 30). The `retrieval` role (spec 0006) works the same way from `RETRIEVAL_LLM_*`, with its own limits (2, 30, 20); it grades and rewrites inside the workflow retrieval stage, and its usage is recorded under agent `knowledge_retrieval_agent`. `chat_enabled` mirrors `CHAT_ENABLED`; the frontend shows the chat button only when it is true. API keys and base URLs are never returned. `GET /api/v1/projects/{project_id}/traces` also returns `llm_roles` with each role's provider and models; its `model` and `provider` fields show the analysis role. Its `total_calls`, token, cost and per agent `traces` figures are the project's real recorded usage (see [Token Usage](#token-usage)).

### Save Settings

```
PUT /api/v1/settings
```

Persists overrides to `override.yaml` and clears settings cache.

**Access**: a signed in user (`401`) with the `Settings` `edit` permission (`403`). Only Superadmin holds it, so nobody else can change `rag.ragflow_api_base` or the key.

**Request Body**: Same shape as settings response, only sections to override. An `llm_roles` section is ignored.

The RAGFlow API key is write only (spec 0006). `GET` returns `rag.ragflow_api_key_set` (true or false), never the key. On save, `rag.ragflow_api_key` is optional: leave it out or empty to keep the current key. A new key applies to the running server and is never written to `override.yaml`; set `RAGFLOW_API_KEY` in `.env` to keep it after a restart. There is no global dataset setting: each project gets its own dataset.

**Response**: `200` — `{"status": "success", "settings": {...}}`

---

## Documents (project knowledge base)

Each project has its own knowledge base (spec 0006). Every route needs a signed in user (401), access to the project (403, or 404 when the project does not exist), and the `Knowledge` permission (403 without it).

### List Documents

```
GET /api/v1/projects/{project_id}/documents
```

Needs `Knowledge:view`.

**Response**: `200`, an array of `Document`. Each document has `embedding_status` (`pending`, `processing`, `completed`, or `failed`), `remote_doc_id` (the RAGFlow document ID, or null), and `error` (why it failed or is waiting, or null).

### Upload Document

```
POST /api/v1/projects/{project_id}/documents/upload
```

Needs `Knowledge:create`. Send one file as `multipart/form-data` in the field `file`.

- Allowed types come from the file extension (`RAG_UPLOAD_TYPES`, default `pdf,docx,txt,md,csv,xlsx`). The content type the browser sends is only a hint.
- The file is saved to `<RAG_STORAGE_ROOT>/<project_id>/documents/<document_id>/<sanitized name>`. `filename` holds the sanitized name and `original_filename` holds yours.
- Indexing runs in the background. The document starts `pending`, becomes `processing`, then `completed` once RAGFlow finishes parsing, or `failed` with the reason in `error`. With `RAG_ENABLED=false` it stays `pending` with `error` "knowledge base is disabled".

**Response**: `201`, the new `Document`.

**Errors**: `413` larger than `RAG_UPLOAD_MAX_MB` (default 20), `415` type not allowed, `422` empty file.

The old JSON route `POST /api/v1/projects/{project_id}/documents` is removed (405).

### Delete Document

```
DELETE /api/v1/projects/{project_id}/documents/{document_id}
```

Needs `Knowledge:delete`. Access and permission are checked before the lookup, so a document in another project returns the same 404 as one that does not exist.

- The record and its saved file are deleted right away.
- Its RAGFlow document is then deleted in the background, from this project's dataset only. If that fails it is logged, and the local delete still stands.
- Deleting a document while it is still uploading leaves no RAGFlow copy behind.

**Response**: `200`, `{"detail": "Document deleted successfully"}`

### Knowledge Status

```
GET /api/v1/projects/{project_id}/knowledge/status
```

Needs `Knowledge:view`.

**Response**: `200`

```json
{
  "enabled": true,
  "dataset_id": "c1f0...",
  "documents": { "completed": 3, "pending": 1 },
  "entries": { "test_case": { "indexed": 12 }, "jira_story": { "indexed": 2, "failed": 1 } },
  "last_indexed_at": "2026-11-17T10:42:00Z",
  "pending_deletes": { "count": 1, "oldest_created_at": "2026-11-18T08:15:00Z", "last_error": "RAGFlow returned 503" }
}
```

- `enabled` is whether the knowledge base is on (`RAG_ENABLED` with the `ragflow` provider).
- `dataset_id` is the project's RAGFlow dataset, or null before anything has been indexed.
- `documents` counts documents by `embedding_status`.
- `entries` counts indexed project sources by `source_type`, then by status (`indexed` or `failed`).
- `last_indexed_at` is the latest of a completed document's `uploaded_at` and a knowledge entry's `indexed_at`, or null.
- `pending_deletes` describes this project's RAGFlow deletes that are still outstanding (spec 0007). `count` is how many there are, `oldest_created_at` is when the oldest was queued, and `last_error` is the error of the most recently attempted one. With none, it is `{"count": 0, "oldest_created_at": null, "last_error": null}`. The UI does not show it yet.

### Indexing behavior

- When a RAGFlow call fails (connection error, timeout, or a 5xx response), indexing retries after each delay in `RAG_INDEX_RETRY_DELAYS` (default 10, 60, and 300 seconds), then marks the document `failed` with the last error.
- A document whose saved file is missing fails with "file not found" and is not retried.
- On startup, every document still `pending` or `processing` is queued again, when the knowledge base is enabled.
- Every RAGFlow delete (a deleted document, a removed or replaced knowledge entry, a deleted project's dataset, a remote copy left by a failed parse, and an extra dataset from a creation race) is saved as a pending delete before it runs. It stays until RAGFlow confirms the delete or says the item is already gone. Failed deletes are retried on the same delays, then again at startup and every `RAG_DELETE_RESWEEP_MINUTES` (default 30). They are never given up on. While the knowledge base is disabled they wait untouched.
- When parsing fails or times out, the document's remote copy is deleted and its remote ID cleared, so uploading it again leaves one remote copy, not two.
- Documents created before real uploads existed are marked `failed` with "file was never uploaded, please upload it again". Upload them again.

### Other indexed sources

Besides uploaded files, these project sources are indexed as knowledge entries, one per source, in the background after the save succeeds. Indexing never slows down or fails the save.

| `source_type` | `source_ref` | Indexed after | Removed after |
|---|---|---|---|
| `test_case` | test case ID | test case generation, edit, approve or reject (only `approved` cases stay indexed) | test case delete, regeneration that replaces it, its requirement's delete in chat |
| `jira_story` | Jira issue key | `POST /requirements/import-jira` | its requirement's delete in chat |
| `execution_failure` | execution ID | a failed or errored run finishes its analysis | execution delete, `DELETE /executions/failed` |
| `locator` | object repository page ID | page create or edit, element create, edit, or delete | page delete |
| `defect` | Jira bug key | a run saved with a `jira_bug_id`, Jira sync after a run, `POST /executions/{id}/sync-bug`, and every `POST /webhooks/jira` event for a linked key (any status) | the last live execution or test case linking the key is deleted (execution delete, `DELETE /executions/failed`, test case delete, regeneration, requirement delete in chat) |

- Unchanged content (same sha256) is not sent again. Changed content replaces the old RAGFlow document.
- RAGFlow failures retry like documents do, then the entry is marked `failed` with the error.
- Defects live only in Jira. A bug key belongs to a project while a live execution (`jira_bug_id`) or test case (`jira_issue_key`) of that project links it; keys are trimmed and matched exactly. The worker reads the key, summary, status, priority, and description from Jira when the job runs. A failed Jira read retries like a RAGFlow failure, then marks the entry `failed`, and never removes it. A closed bug stays indexed with its new status. Without Jira credentials (mock mode), defects are skipped and nothing is written.
- To backfill or repair a project, run `python -m backend.scripts.reindex_project_knowledge --project <id>` (or `--all`). It indexes every current source and pending document, removes entries whose source is gone, and prints counts of indexed, skipped (unchanged), failed, and removed items.
- To see outstanding RAGFlow deletes for every project, deleted ones included, run `python -m backend.scripts.reindex_project_knowledge --pending-deletes`. On PostgreSQL it also tries each one once. On the JSON store it only lists them unless you add `--run`, and you should stop the server first. It prints `deleted N, still pending M` and exits 1 while any remain.
- To measure retrieval quality, run `python -m backend.scripts.eval_retrieval --project <id> --cases <file.json> [--k 5] [--json]` (spec 0006, AC-25). The cases file is a JSON list of `{query, expected_source_refs, source_types?}`; an expected ref is `<source_ref>` or `<source_type>:<source_ref>`, and `k` is 1 to 10. It searches the project's live knowledge base exactly as chat and the workflow do, and prints recall@k and MRR per case and overall. `backend/tests/fixtures/retrieval_eval/corpus.json` shows the format.
- Test case responses carry `groundedness_score` (0 to 1, or `null`) and `unsupported_claims` (a list of short claims). `EvaluationAgent` sets them only when the workflow retrieval stage found context; they are reported, never enforced (spec 0006, AC-26).

---

## Auth

### Login

```
POST /api/v1/auth/login
```

```json
{
  "email": "admin@agenticai.com",
  "password": "admin123"
}
```

**Response**: `200`

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "admin@agenticai.com",
    "name": "Admin User",
    "role": "admin"
  }
}
```

### Refresh Token

```
POST /api/v1/auth/refresh
```

```json
{
  "refresh_token": "eyJ..."
}
```

**Response**: `200` — `{"access_token": "eyJ...", "token_type": "bearer"}`

---

## Error Responses

| Status | Meaning |
|---|---|
| `400` | Invalid input (validation error) |
| `401` | Missing or expired JWT token |
| `403` | Insufficient permissions (RBAC) |
| `404` | Resource not found |
| `500` | Internal server error (LLM failure, file parse error, etc.) |

Error body:

```json
{
  "detail": "Human-readable error message"
}
```
