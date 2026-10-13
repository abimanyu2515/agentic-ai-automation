# Agentic AI Automation

## Stack

* **Language and runtime**: Python 3.12 or newer, JavaScript, TypeScript
* **Framework**: FastAPI, LangGraph, vanilla HTML, CSS, and JavaScript
* **Key dependencies**: LiteLLM, LangGraph, SQLAlchemy, Playwright, pytest
* **Package manager**: pip for Python, pnpm for the Playwright workspace

## Build approach

<TBD, set by /scope>

## Commands

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Playwright workspace dependencies
pnpm --dir backend/playwrightt install

# Start the backend and served frontend
uvicorn backend.main:app --reload --port 8000

# Build the Playwright execution workspace
pnpm --dir backend/playwrightt build

# Run backend tests
pytest -q
```

## Specs

Feature documentation is stored in `docs/`. Architecture specifications may be added under `docs/specs/`.

## Rules

* Keep the agent contract centered on `WorkflowState`. Each agent validates its inputs, logs its start and completion, updates only its owned fields, and returns the state.
* Keep API routes in `backend/main.py` thin. Put business behavior in services, agents, repositories, or domain models.
* Use the repository abstraction for project, requirement, test case, and execution persistence. The JSON store is the default and PostgreSQL is optional.
* Treat project target URLs, frameworks, and credentials as project data. Do not add a global application URL or demo application fallback.
* Keep credentials in `VaultService` and pass them to runners through environment variables.
* Use dependency injection in `backend/graph/workflow.py` so agent and runner tests do not require live LLM or browser calls.
* Use pytest for Python tests and Playwright tests for browser behavior. Keep generated artifacts and local databases out of source changes.
* After any SQLAlchemy database error, roll back and clear the session before another database operation. Do not reuse a failed transaction.
* Update the relevant documentation when an API route, workflow stage, project setting, or execution behavior changes.

## Agent skills

Declined: vercel React best practices, Next.js best practices, web design guidelines, Vercel agent browser, Playwright MCP, Atlassian Remote MCP, LangSmith Remote MCP, Context7 MCP

## Context files

* [backend/AGENTS.md](backend/AGENTS.md): Backend API, workflow, persistence, agent, and execution conventions
* [frontend/AGENTS.md](frontend/AGENTS.md): Single page application and API client conventions
* [backend/database/AGENTS.md](backend/database/AGENTS.md): Database providers, sessions, and migrations
* [backend/repository/AGENTS.md](backend/repository/AGENTS.md): Repository transactions and persistence mappings
* [backend/tests/AGENTS.md](backend/tests/AGENTS.md): Backend test doubles and project configuration fixtures

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
