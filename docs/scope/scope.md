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
| 3 | Project creation with target URL | Project setup | done |
| 4 | Role based LLM routing | LLM reliability | in-progress |
| 5 | Object repository | Locator reliability | in-progress |
| 6 | Chat assistant | Assistant | in-progress |
| 7 | Permission checks on data changing routes | Assistant | planned |
| 8 | Agentic RAG | Project knowledge | done |
| 9 | Jira knowledge sources | Project knowledge | in-progress |
| 10 | Failure analysis for UI and batch runs | Project knowledge | planned |

## Existing product

### 1. Project management · existing
Users can create, select, update, and delete projects, and configure project settings and credentials. Code area: `frontend/index.html`, `frontend/js/app.js`, `frontend/js/api.js`, `backend/main.py`, `backend/services/project_service.py`, and `backend/repository/`.

### 2. Requirement to execution workflow · existing
The application processes requirements through scenario generation, test case creation, script generation, execution, analysis, reporting, and synchronization. Code area: `backend/agents/`, `backend/graph/workflow.py`, and `backend/services/workflow_service.py`.

## Project setup

### 3. Project creation with target URL · done
Let internal QA users provide the target application URL while creating a project, so the project can satisfy its required configuration before it is saved and used for testing.
**Done when:** the create form collects a valid absolute HTTP or HTTPS URL, the request sends it, the backend validates and persists it consistently with the database, invalid input shows a clear error, and the form remains usable with keyboard and assistive technology.
- [x] Design it (spec): `/architect project creation with target URL`
- Spec: [0002](../specs/0002-project-creation-target-url/index.md)
- Code: `backend/main.py`, `backend/schemas/project_dto.py`, `backend/repository/`, `backend/database/`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/api.js`
- [x] Build it: `/develop project creation with target URL`
  - [x] Wire the target URL form, API payload, validation errors, and readiness response, covering AC-1, AC-2, AC-3, AC-4, AC-6, and AC-10
  - [x] Add authenticated ownership, Projects create permission, Jira key retention, and forbidden field rejection, covering AC-7 and AC-8
  - [x] Enforce owner scoped normalized URL uniqueness in JSON and PostgreSQL with rollback, locking, migration, and conflict responses, covering AC-5 and AC-8
  - [x] Remove the demo URL fallback and add focused backend coverage for validation, conflicts, ownership, and readiness, covering AC-1 through AC-8
  - [x] Add rollback, logging, migration, and browser accessibility coverage, covering AC-8, AC-9, and AC-10
- [x] Verify it: `/check verify project creation with target URL`
- [x] Test it: `/test project creation with target URL`

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

## Assistant

### 6. Chat assistant · in-progress
A floating chat button on every screen. The assistant answers questions about the selected project's data, explains how the app works, and proposes generate, run, create, edit, and delete actions that run only after the user confirms each one on a card.
**Done when:** replies stream from any view with project and screen context, threads are saved per user per project on both stores and purged after 90 idle days, every catalog action runs only after confirm with its permission checked (deletes need `DELETE` typed), action cards track live status and refresh the view behind them, and failures, Stop, retry, and rate limits behave as specified.
- [x] Design it (spec): `/architect chat assistant`
- Spec: [0005](../specs/0005-chat-assistant/index.md)
- Code: `backend/services/chat_service.py`, `backend/services/chat_tools.py`, `backend/services/dashboard_metrics.py`, `backend/services/chat_actions.py`, `backend/services/llm.py`, `backend/services/execution_service.py`, `backend/repository/`, `backend/database/`, `backend/prompts/agents/chat_assistant/`, `backend/main.py`, `frontend/js/chat.js`, `frontend/js/api.js`, `frontend/js/app.js`, `frontend/css/styles.css`, `frontend/index.html`
- [x] Build it: `/develop chat assistant`
  - [x] One streamed answer end to end: chat role, streaming LLM call, both stores and migration, access helpers, thread routes, floating panel with Markdown and the open item context line, covering AC-1, AC-2, AC-4 to AC-7, AC-9, AC-21, AC-23 to AC-25, AC-27
  - [x] Answers over project data with read tools, step and size caps, covering AC-8 and AC-9
  - [x] One action end to end (`generate_scenarios`): propose, confirm, status, expiry, audit, view refresh, restart recovery, covering AC-10 to AC-12 and AC-14 to AC-16, AC-22 (release point)
  - [x] The rest of the action catalog plus the typed `DELETE` confirm, with execution start moved into services, covering AC-11, AC-13, and AC-15
  - [x] Failure, Stop, retry, stream cap, rate limit, help mode, retention, the enable flag, tests, and docs, covering AC-1, AC-3, AC-17 to AC-20, AC-24, and AC-26
- [ ] Verify it: `/check verify chat assistant`
- [x] Test it: `/test chat assistant`

### 7. Permission checks on data changing routes · planned · from spec 0005
Add `has_permission` checks to the routes that change data without one today (generate scenarios, generate test cases, execute, batch execute, scenario and test case edit and delete), so the UI enforces the same rules the chat assistant does.
**Done when:** each of those routes checks its seeded module and action, and a user without the permission gets 403 in the UI as in chat.
- [ ] Design it (spec): `/architect permission checks on data changing routes`

## Project knowledge

### 8. Agentic RAG · done
Give each project its own RAGFlow knowledge base with real file uploads, let the chat assistant search it, and add a graded, self correcting retrieval stage that grounds the scenario, test case, requirement, analysis, and defect agents, with recall and groundedness measured.
**Done when:**
- real files upload into a per project dataset and reach `completed`, and searches and deletes never cross projects
- `search_knowledge` answers in chat
- test cases, failures, and locators are indexed (Jira stories and defects moved to feature 9)
- the retrieval stage feeds all five agents behind its flag and never fails a run
- recall@k, MRR, and groundedness are reported, and LangSmith shows each search, grade, and rewrite

- [x] Design it (spec): `/architect agentic RAG`
- Spec: [0006](../specs/0006-agentic-rag/index.md) · child [0007](../specs/0006-agentic-rag/0007-persist-pending-ragflow-deletes.md) (persist pending RAGFlow deletes)
- Code: `backend/services/retrieval/`, `backend/services/retrieval_eval_service.py`, `backend/scripts/eval_retrieval.py`, `backend/agents/evaluation_agent.py`, `backend/services/knowledge_service.py`, `backend/services/knowledge_indexer.py`, `backend/services/knowledge_sources.py`, `backend/scripts/reindex_project_knowledge.py`, `backend/agents/knowledge_retrieval_agent.py`, `backend/graph/workflow.py`, `backend/agents/supervisor_agent.py`, `backend/services/chat_tools.py`, `backend/models/`, `backend/repository/`, `backend/database/`, `backend/config/`, `backend/main.py`, `frontend/js/app.js`, `frontend/js/api.js`
- [x] Build it: `/develop agentic RAG`
  - [x] Index failed runs from every save path (task 12b, found by `/check verify` on 2026-11-18): the hook in the UI webhook and batch save paths, plus a backfill, covering AC-14 and AC-15
  - [x] One real file end to end into chat: retriever contract, per project dataset, upload route and indexer, real upload UI, `search_knowledge`, covering AC-1 to AC-3, AC-6, AC-11 to AC-13
  - [x] Safe and complete knowledge base: permissions, safe delete, retries and startup sweep, status route, old document migration, project cleanup, settings key, covering AC-2, AC-4 to AC-10
  - [x] Index the rest of the project: `KnowledgeEntry`, hooks, reindex script, the defect source read from Jira, covering AC-14, AC-15, and AC-16 (the Jira parts, once AC-14a, are verified under feature 9)
  - [x] Retrieval stage feeding all five agents: retrieval role, `project_id` on state, the graded loop, graph wiring behind the flag, prompt blocks, feature inventory migration, covering AC-17 to AC-23 and AC-20a
  - [x] Measure and trace: LangSmith spans, retrieval eval, groundedness, covering AC-24 to AC-26
  - [x] Persist pending RAGFlow deletes (spec 0007): a document delete that survives a restart, then every delete path with the resweep, then the status field and the `--pending-deletes` script, covering 0007 AC-1 to AC-13
- [x] Verify it: `/check verify agentic RAG`
- [x] Test it: `/test agentic RAG`

### 9. Jira knowledge sources · in-progress · from spec 0006
Index imported Jira stories and the Jira bugs linked to a project's runs into its knowledge base, and prove it against a working Jira. Split from Agentic RAG on 2026-11-18 because the Jira site was unavailable; the code is already built.
**Done when:**
- an imported Jira story is indexed and found by search, and removed with its requirement
- a linked Jira bug is indexed with its current status, stays when closed, is removed when no link is left, and never shows in another project
- the reindex script indexes stories and linked bug keys once each

- [x] Design it (spec): `/architect agentic RAG`
- Spec: [0008](../specs/0008-jira-knowledge-sources/index.md)
- Code: `backend/services/knowledge_sources.py`, `backend/services/knowledge_indexer.py`, `backend/scripts/reindex_project_knowledge.py`, `backend/main.py` (Jira import, sync bug, and Jira webhook routes), `backend/services/jira_sync_service.py`, `backend/services/chat_actions.py`
- [x] Build it: `/develop jira knowledge sources` (built under spec 0006)
  - [x] Story hook on the Jira import and removal on the chat requirement delete, covering AC-1
  - [x] Defect reader, live link check, and the four enqueue hooks, covering AC-2
  - [x] Reindex script story and defect keys, covering AC-3
- [ ] Verify it: `/check verify jira knowledge sources`
- [ ] Test it: `/test jira knowledge sources`

### 10. Failure analysis for UI and batch runs · planned · from spec 0006
Run the AI failure analysis (with its knowledge retrieval stage) on failed runs from the UI and from batch runs, which today are saved without a failure category or root cause summary, so their knowledge entries and reports explain why a run failed.
**Done when:** a failed UI or batch run gets a failure category and root cause summary in the background, its knowledge entry is indexed again with that summary, and a slow or failing LLM never blocks or breaks the saved result.
- [ ] Design it (spec): `/architect failure analysis for UI and batch runs`

## Deferred

- Chat assistant keyboard and screen reader support (from spec 0005)
- Shared chat locks and rate limits for running more than one worker (from spec 0005)
- Running summary of older chat turns for long threads (from spec 0005)
- Groundedness score on the test case detail view (from spec 0006)
- pgvector retriever for running without RAGFlow (from spec 0006)
- Persistent indexing queue and support for more than one server process (from spec 0006)

## Legend

**Feature lifecycle:** `planned` means the feature is queued, `existing` means it predates this workflow, `in-progress` means work is underway, `done` means this workflow built and verified it, and `dropped` means it is no longer active but remains recorded.

**Needs a decision:** Run `/architect` when the feature needs a load bearing decision. This feature needs a spec because URL validation, database constraints, API behavior, and project readiness must remain consistent across existing frontend and backend paths.

**Next step:** The first unticked box is the next recommended action.
