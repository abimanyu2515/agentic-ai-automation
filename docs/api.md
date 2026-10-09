# API Reference

Base URL: `/api/v1`

Authentication: JWT Bearer token in `Authorization` header.

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
  "target_url": "https://adactinhotelapp.com/",
  "target_username": "user",
  "target_password": "pass"
}
```

**Validation**: `name` and `description` must be non-empty strings.

**Response**: `201` — `ProjectResponse`

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

**Response**: `200` — `ProjectResponse`, `404` if not found.

---

### Delete Project

```
DELETE /api/v1/projects/{project_id}
```

**Response**: `200` — `{"detail": "Project deleted successfully"}`, `404` if not found.

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

**Response**: `200` — `TestCaseResponse`, `404` if not found.

---

### Delete Test Case

```
DELETE /api/v1/projects/{project_id}/testcases/{test_case_id}
```

**Response**: `200` — `{"detail": "Test case deleted successfully"}`

---

### Update Playwright Script

```
PUT /api/v1/projects/{project_id}/testcases/{test_case_id}/script
```

**Request Body** (`ScriptUpdatePayload`):

```json
{
  "script": "import { test, expect } from '@playwright/test';\n// ..."
}
```

**Response**: `200` — `TestCaseResponse`, `404` if not found.

---

## Executions

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

Runs ExecutionAgent → ExecutionAnalysisAgent → DefectManagementAgent → ReportAgent pipeline.

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
  "failure_category": "string|null",
  "root_cause_summary": "string|null",
  "jira_bug_id": "string|null",
  "jira_bug_url": "string|null",
  "retest_pending_candidate": false,
  "test_cycle_id": "uuid|null",
  "executed_by": "uuid|null"
}
```

---

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

Returns decrypted username and password from vault. Falls back to first non-null vault entry, then hardcoded defaults.

**Response**: `200`

```json
{
  "username": "ADACTINFORQA",
  "password": "hanish13"
}
```

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
  "workflow": { "mode": "sequential", "human_review_enabled": true, ... },
  ...
}
```

### Save Settings

```
PUT /api/v1/settings
```

Persists overrides to `override.yaml` and clears settings cache.

**Request Body**: Same shape as settings response, only sections to override.

**Response**: `200` — `{"status": "success", "settings": {...}}`

---

## Documents

### List Documents

```
GET /api/v1/projects/{project_id}/documents
```

**Response**: `200` — Array of `Document`

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
