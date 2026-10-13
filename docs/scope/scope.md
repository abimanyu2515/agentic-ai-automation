# Scope: Project setup and automation

This application helps internal QA teams create projects and turn requirements into automated tests and execution reports.

**Build approach:** Tracer Bullet (carry one working flow through the user interface, API, validation, and persistence).
**Workflow:** Beta (verify the real application, then add focused tests).

_These recommendations keep the build orderly. You can skip any step that does not fit._

## At a glance

| # | Feature | Phase | Status |
|---|---------|-------|--------|
| 1 | Project management | Existing product | existing |
| 2 | Requirement to execution workflow | Existing product | existing |
| 3 | Project creation with target URL | Project setup | in-progress |

## Existing product

### 1. Project management · existing
Users can create, select, update, and delete projects, and configure project settings and credentials. Code area: `frontend/index.html`, `frontend/js/app.js`, `frontend/js/api.js`, `backend/main.py`, `backend/services/project_service.py`, and `backend/repository/`.

### 2. Requirement to execution workflow · existing
The application processes requirements through scenario generation, test case creation, script generation, execution, analysis, reporting, and synchronization. Code area: `backend/agents/`, `backend/graph/workflow.py`, and `backend/services/workflow_service.py`.

## Project setup

### 3. Project creation with target URL · in-progress
Let internal QA users provide the target application URL while creating a project, so the project can satisfy its required configuration before it is saved and used for testing.
**Done when:** the create form collects a valid absolute HTTP or HTTPS URL, the request sends it, the backend validates and persists it consistently with the database, invalid input shows a clear error, and the form remains usable with keyboard and assistive technology.
- [x] Design it (spec): `/architect project creation with target URL`
- Spec: [0002](../specs/0002-project-creation-target-url/index.md)
- Code: `backend/main.py`, `backend/schemas/project_dto.py`, `backend/repository/`, `backend/database/`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/api.js`
- [ ] Build it: `/develop project creation with target URL`
  - [x] Wire the target URL form, API payload, validation errors, and readiness response, covering AC-1, AC-2, AC-3, AC-4, AC-6, and AC-10
  - [x] Add authenticated ownership, Projects create permission, Jira key retention, and forbidden field rejection, covering AC-7 and AC-8
  - [ ] Enforce owner scoped normalized URL uniqueness in JSON and PostgreSQL with rollback, locking, migration, and conflict responses, covering AC-5 and AC-8
  - [x] Remove the demo URL fallback and add focused backend coverage for validation, conflicts, ownership, and readiness, covering AC-1 through AC-8
  - [ ] Add rollback, logging, migration, and browser accessibility coverage, covering AC-8, AC-9, and AC-10
- [ ] Verify it: `/check verify project creation with target URL`
- [ ] Test it: `/test project creation with target URL`

## Legend

**Feature lifecycle:** `planned` means the feature is queued, `existing` means it predates this workflow, `in-progress` means work is underway, `done` means this workflow built and verified it, and `dropped` means it is no longer active but remains recorded.

**Needs a decision:** Run `/architect` when the feature needs a load bearing decision. This feature needs a spec because URL validation, database constraints, API behavior, and project readiness must remain consistent across existing frontend and backend paths.

**Next step:** The first unticked box is the next recommended action.
