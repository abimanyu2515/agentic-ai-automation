# Backend

## Overview

The backend exposes the FastAPI application and coordinates the requirement to execution workflow. It combines LangGraph agents, domain models, services, repository implementations, encrypted project credentials, and Playwright execution.

## Key files

| File | Owns |
|---|---|
| `backend/main.py` | FastAPI startup, authentication, project routes, workflow routes, and execution routes |
| `backend/graph/workflow.py` | LangGraph state machine construction and agent routing |
| `backend/services/workflow_service.py` | Service entry points for generation, script creation, and execution |
| `backend/agents/` | Agents that read and update their assigned `WorkflowState` fields |
| `backend/repository/` | JSON and PostgreSQL persistence through one repository interface |
| `backend/services/playwright_runner.py` | The only direct Playwright subprocess integration |
| `backend/services/vault_service.py` | Encrypted project credential storage |
| `backend/tests/` | pytest tests with stub LLM services and fake browser runners |

## Commands

```bash
# Run backend tests
pytest -q

# Run one test file
pytest -q backend/tests/test_api.py

# Start the API
uvicorn backend.main:app --reload --port 8000
```

## Conventions

* Agents implement `BaseAgent.run(state: WorkflowState) -> WorkflowState`.
* Agents must validate preconditions, log start and completion, and raise `ValueError` for invalid workflow state.
* `WorkflowService` validates project and requirement ownership before invoking a graph operation.
* Project settings own `target_url` and `framework`. Credentials belong in the vault and are resolved with the project ID.
* Use `get_project_repository()` rather than constructing a repository inside application logic.
* Use injected agents, LLM services, and runners in tests. Do not require a live provider or browser for unit tests.
* Keep route response models in `backend/schemas/` and domain objects in `backend/models/`.
* Capture the selected project configuration before queueing work. Persist its normalized URL, framework, project ID, and credential reference with each execution result. Never persist passwords.

## Gotchas

* The default repository provider is JSON. The SQLAlchemy database also exists for authentication and optional PostgreSQL persistence.
* Playwright scripts are generated and executed through the separate `backend/playwrightt` workspace. Runtime artifacts are rooted under `backend/playwrightt/artifacts/`.
* A project URL must come from the selected project. `backend/config/default_config.yaml` intentionally has an empty global Playwright URL.
* The runner receives project credentials through environment variables. If a generated script needs a URL, it must use the project value or an explicitly supplied runtime value.
* PostgreSQL uses a scoped SQLAlchemy session. A failed query or write must be followed by rollback and session removal before another repository operation.
* Avoid editing generated artifacts, local databases, vault data, or cached files unless the task specifically targets them.

## Context files

* [playwrightt/AGENTS.md](playwrightt/AGENTS.md): Next.js and Playwright execution workspace conventions

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
