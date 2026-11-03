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
| 4 | Role based LLM routing | LLM reliability | in-progress |
| 5 | Object repository | Locator reliability | in-progress |

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

## LLM reliability

### 4. Role based LLM routing · in-progress
Send test data and script generation to Gemini (rotating between two models, at most two calls at a time, with a pause after rate limits) and every other agent to NVIDIA, so bursts finish instead of exhausting one provider's request limit.
**Done when:** each caller reaches its role's provider, generation calls rotate, queue and recover from 429s without using up retries, unset role values fall back to `LLM_*`, and Master Settings shows both roles without keys.
- [x] Design it (spec): `/architect role based LLM routing`
- Spec: [0003](../specs/0003-role-based-llm-routing/index.md)
- Code: `backend/services/llm.py`, `backend/config/settings.py`, `backend/agents/`, `backend/services/report_service.py`, `backend/services/execution_analyzer.py`, `backend/main.py`, `frontend/`
- [x] Build it: `/develop role based LLM routing`
  - [x] Role config with fallback, and TestDataAgent on Gemini end to end, covering AC-1 and AC-9
  - [x] Every caller on its role, covering AC-1, AC-2 and AC-12
  - [x] Shared role pool with rotation, cap, pacing, 429 failover and deadline, covering AC-3 through AC-8 and AC-13
  - [x] Logging, LangSmith tags, settings endpoints and Master Settings display, covering AC-10 and AC-11
- [ ] Verify it: `/check verify role based LLM routing`
- [ ] Test it: `/test role based LLM routing`

## Locator reliability

### 5. Object repository · in-progress
Store each project's pages, UI elements, and ordered locators so generated scripts reference elements by name, one locator fix reaches every script, and run feedback shows which locators are going stale.
**Done when:** recon and testers fill the repository, generated scripts use `orx` keys with locators injected at read and run time, runs update element health on both execution paths, the Object Repository view lets permitted users browse, edit, and scan, on both the JSON store and PostgreSQL, and test runs on both runners harvest the pages they visit (add and update only, redacted).
- [x] Design it (spec): `/architect object repository`
- Spec: [0004](../specs/0004-object-repository/index.md)
- Code: `backend/services/`, `backend/repository/`, `backend/database/`, `backend/agents/application_reconnaissance_agent.py`, `backend/agents/playwright_agent.py`, `backend/agents/execution_agent.py`, `backend/services/execution_service.py`, `backend/playwrightt/lib/execution-engine.ts`, `backend/playwrightt/lib/object-harvest.js`, `backend/playwrightt/lib/object-harvest-session.ts`, `backend/main.py`, `frontend/`
- [x] Build it: `/develop object repository`
  - [x] One manual element end to end: tables, both stores, service and create/read API, permissions, render and strip, prompt and key validation, minimal view, covering AC-4, AC-6, AC-7, AC-8, AC-9, AC-11, AC-12, and AC-14
  - [x] Run feedback from both execution paths into element and locator health, covering AC-10
  - [x] Recon ingest with merge rules and locking, plus the Scan now job, covering AC-1, AC-2, AC-3, and AC-15
  - [x] Edit, rename rewrite, delete, the full view, docs, and test coverage, covering AC-4, AC-5, AC-12, and AC-13
  - [x] Harvest during execution: shared extractor, harvest ingest with redaction, Python runner file transport, workspace engine payload, covering AC-16, AC-17, AC-18, AC-19, AC-20, and AC-21
- [ ] Verify it: `/check verify object repository`
- [ ] Test it: `/test object repository`

## Legend

**Feature lifecycle:** `planned` means the feature is queued, `existing` means it predates this workflow, `in-progress` means work is underway, `done` means this workflow built and verified it, and `dropped` means it is no longer active but remains recorded.

**Needs a decision:** Run `/architect` when the feature needs a load bearing decision. This feature needs a spec because URL validation, database constraints, API behavior, and project readiness must remain consistent across existing frontend and backend paths.

**Next step:** The first unticked box is the next recommended action.
