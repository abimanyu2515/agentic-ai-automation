# Agentic AI Automation — AI-Powered Test Automation Platform

An enterprise-grade AI test automation platform that uses a **LangGraph-powered multi-agent system** to ingest requirements, generate test scenarios, create test cases, produce Playwright automation scripts, execute them, and report results — all orchestrated through a FastAPI backend with a single-page frontend.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Key Features](#key-features)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [API Summary](#api-summary)
- [Multi-Agent Pipeline](#multi-agent-pipeline)
- [Testing](#testing)
- [Documentation](#documentation)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (HTML/CSS/JS)                    │
│              Chart.js Dashboard · Monaco Editor              │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP / JSON
┌───────────────────────────▼─────────────────────────────────┐
│                  FastAPI Backend (main.py)                   │
│            REST API · CORS · JWT Auth · RBAC                │
└───────┬──────────┬──────────┬──────────┬───────────────────┘
        │          │          │          │
┌───────▼──┐ ┌─────▼─────┐ ┌─▼────────┐ ┌▼──────────────┐
│ Services │ │  Agents   │ │  Models  │ │  Repository   │
│   (18)   │ │   (17)    │ │  (13)    │ │  (Abstract)   │
└──────────┘ └─────┬─────┘ └──────────┘ └───┬───────────┘
                   │                         │
          ┌────────▼────────┐       ┌────────▼────────┐
          │  LangGraph      │       │  JSON File      │
          │  StateGraph     │       │  / PostgreSQL   │
          └─────────────────┘       └─────────────────┘
```

The platform is built on a **LangGraph state machine** where each agent owns a slice of `WorkflowState`. The supervisor agent routes execution based on operation type, and services orchestrate graph invocations from the API layer.

---

## Key Features

- **Multi-Provider LLM Support** — OpenAI, Ollama, Anthropic Claude, Gemini, Groq via LiteLLM
- **Requirement Ingestion** — Parse PDF, DOCX, XLSX, CSV files; import JIRA stories
- **AI Scenario Generation** — Generate test scenarios from requirements with configurable count
- **AI Test Case Generation** — Create detailed test cases with steps, preconditions, expected results
- **Quality Evaluation** — Duplicate detection (Jaccard similarity), relevance scoring, confidence ranking
- **Human Review Gates** — Approve/reject scenarios and test cases before automation
- **Playwright Script Generation** — Auto-generate TypeScript Playwright scripts for approved test cases
- **Test Execution** — Run Playwright tests with real-time streaming logs, screenshots, video, traces
- **Execution Analysis** — Root cause analysis, flakiness detection, failure categorization
- **JIRA Integration** — Sync scenarios as Stories, raise Bugs from failures, bi-directional status sync
- **Dashboard Analytics** — Pass rates, coverage, trends, defect tracking, priority distribution
- **RBAC** — Admin, Manager, Engineer, Viewer roles with permission-based access
- **Encrypted Vault** — Secure credential storage for target application logins
- **RAG Support** — Optional RAGFlow / Chroma knowledge base for context-aware processing
- **LangSmith Tracing** — Full observability of LLM calls, agent decisions, and execution paths
- **Audit Logging** — All mutations logged with user, action, and timestamp

---

## Tech Stack

| Layer | Technology |
|---|---|
| **API Framework** | FastAPI 0.116 |
| **Python** | 3.12+ |
| **LLM Integration** | LiteLLM 1.90 (multi-provider) |
| **Workflow Engine** | LangGraph 1.2 (StateGraph) |
| **Tracing / Observability** | LangSmith 0.9 |
| **Database (Primary)** | JSON file store (PostgreSQL via SQLAlchemy optional) |
| **Auth** | JWT (python-jose) + bcrypt + passlib |
| **File Parsing** | PyMuPDF, pandas (XLSX/CSV), python-docx |
| **Test Automation** | Playwright 1.61 (TypeScript, Next.js runner) |
| **RAG / Vector DB** | RAGFlow / Chroma / Qdrant |
| **JIRA Integration** | Atlassian REST API via httpx |
| **Frontend** | Vanilla HTML/CSS/JS, Chart.js, Monaco Editor |
| **Database Migrations** | Alembic |
| **Testing** | pytest, pytest-cov |

---

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+ (for Playwright test runner)
- An LLM provider: OpenAI key, or Ollama running locally

### Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd Agentic-AI-Automation

# 2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/Mac

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env with your LLM provider and API keys

# 5. Install Playwright browser
npx playwright install chromium
# or: pip install playwright && playwright install chromium

# 6. Run the server
uvicorn backend.main:app --reload --port 8000
```

The application will be available at `http://localhost:8000`. The frontend is served as static files at the root. To use **Run Selected Batch** or **Generate Test Data & Run Tests**, start the separate Playwright workspace in another terminal:

```bash
pnpm --dir backend/playwrightt install
pnpm --dir backend/playwrightt dev
```

The workspace is available at `http://localhost:3000` by default. The UI reports when this server is unavailable.

### Quick Start

1. Open `http://localhost:8000` in your browser
2. Sign in with default admin credentials (seeded on first run):
   - Email: `admin@agenticai.com`
   - Password: `admin123`
3. Create a new project workspace
4. Add requirements manually or import from file
5. Generate scenarios, then test cases
6. Approve and execute automated tests

---

## Configuration

Configuration uses a layered approach:

1. **`backend/config/default_config.yaml`** — Default values (committed to repo)
2. **`.env`** — Secrets and environment-specific overrides (never committed)
3. **`backend/config/override.yaml`** — Runtime overrides via Settings UI (auto-generated)

Key environment variables (`.env`):

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | LLM provider (openai, ollama, groq, anthropic, gemini) | openai |
| `LLM_MODEL` | Model name | gpt-4o-mini |
| `LLM_API_KEY` | API key | — |
| `LLM_API_BASE` | API base URL (required for Ollama) | — |
| `LANGSMITH_TRACING` | Enable LangSmith tracing | false |
| `LANGSMITH_API_KEY` | LangSmith API key | — |
| `RAG_ENABLED` | Enable RAG knowledge base | false |
| `RAGFLOW_API_BASE` | RAGFlow API URL | http://localhost:9380 |
| `REPOSITORY_PROVIDER` | Database provider (json or postgresql) | json |
| `DATABASE_URL` | PostgreSQL connection string | — |
| `JIRA_BASE_URL` | JIRA instance URL | — |
| `JIRA_EMAIL` | JIRA account email | — |
| `JIRA_API_TOKEN` | JIRA API token | — |

Settings can also be modified at runtime via the **Settings** panel in the UI or the `/api/v1/settings` endpoint.

---

## Project Structure

```
Agentic-AI-Automation/
├── backend/
│   ├── main.py                  # FastAPI application + all routes
│   ├── agents/                  # LangGraph agent nodes (17 agents)
│   │   ├── base.py              # Abstract BaseAgent
│   │   ├── supervisor_agent.py  # Routing and orchestration
│   │   ├── scenario_agent.py    # Test scenario generation
│   │   ├── test_case_agent.py   # Test case generation
│   │   ├── evaluation_agent.py  # Quality evaluation
│   │   ├── human_approval_agent.py
│   │   ├── playwright_agent.py  # Script generation
│   │   ├── execution_agent.py   # Test execution
│   │   ├── execution_analysis_agent.py
│   │   ├── defect_management_agent.py
│   │   ├── report_agent.py
│   │   ├── requirement_analyst_agent.py
│   │   ├── feature_inventory_agent.py
│   │   ├── backlog_creation_agent.py
│   │   ├── jira_sync_agent.py
│   │   ├── qa_story_analyzer_agent.py
│   │   └── automation_orchestrator_agent.py
│   ├── api/                     # (reserved for future route modules)
│   ├── config/
│   │   ├── settings.py          # Configuration loader
│   │   └── app_states.json      # Target app state definitions
│   ├── database/
│   │   ├── db.py                # SQLAlchemy engine + session
│   │   ├── db_models.py         # ORM models
│   │   ├── db_seeder.py         # Default data seeder
│   │   ├── alembic/             # Database migrations
│   │   ├── vault.json           # Encrypted credential vault
│   │   └── project_store.json   # JSON file database
│   ├── dependencies/
│   │   └── auth_dependencies.py # Auth middleware
│   ├── graph/
│   │   └── workflow.py          # LangGraph StateGraph definition
│   ├── models/                  # Pydantic domain models (13)
│   ├── prompts/agents/          # Agent system prompts (14 agent dirs)
│   ├── repository/
│   │   ├── project_repository.py    # Abstract + JSON implementation
│   │   └── postgres_project_repository.py
│   ├── schemas/                 # API DTOs
│   ├── services/                # Business logic (18 services)
│   ├── tests/                   # Test suite (18 test files)
│   └── utils/                   # Utilities (crypto, pom_parser, prompts)
├── frontend/
│   ├── index.html               # Single-page application
│   ├── css/styles.css
│   └── js/
│       ├── api.js               # API client
│       ├── app.js               # Main SPA logic
│       └── components.js        # Reusable UI components
├── docs/                        # Documentation
├── scratch/                     # Utility/debug scripts
├── data/                        # RAG document storage
└── .env.example                 # Environment template
```

---

## API Summary

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/projects` | List all projects |
| POST | `/api/v1/projects` | Create project |
| GET | `/api/v1/projects/{id}` | Get project details |
| PUT | `/api/v1/projects/{id}` | Update project |
| DELETE | `/api/v1/projects/{id}` | Delete project |
| GET | `/api/v1/projects/{id}/requirements` | List requirements |
| POST | `/api/v1/projects/{id}/requirements` | Create requirement |
| POST | `/api/v1/projects/{id}/requirements/import` | Import file |
| POST | `/api/v1/projects/{id}/requirements/import-jira` | Import JIRA story |
| POST | `/api/v1/projects/{id}/requirements/{rid}/generate-scenarios` | Generate scenarios |
| POST | `/api/v1/projects/{id}/requirements/{rid}/generate-backlog` | Generate backlog |
| POST | `/api/v1/projects/{id}/requirements/{rid}/generate-testcases` | Generate test cases |
| GET | `/api/v1/projects/{id}/scenarios` | List scenarios |
| PUT | `/api/v1/projects/{id}/scenarios/{sid}` | Update scenario |
| DELETE | `/api/v1/projects/{id}/scenarios/{sid}` | Delete scenario |
| GET | `/api/v1/projects/{id}/testcases` | List test cases |
| PUT | `/api/v1/projects/{id}/testcases/{tc_id}` | Update test case |
| DELETE | `/api/v1/projects/{id}/testcases/{tc_id}` | Delete test case |
| PUT | `/api/v1/projects/{id}/testcases/{tc_id}/script` | Update Playwright script |
| POST | `/api/v1/projects/{id}/executions` | Execute test |
| GET | `/api/v1/projects/{id}/dashboard-metrics` | Dashboard analytics |
| GET | `/api/v1/settings` | Read settings |
| PUT | `/api/v1/settings` | Save settings |
| POST | `/api/v1/auth/login` | Login |
| POST | `/api/v1/auth/refresh` | Refresh token |

Full API reference: [docs/api.md](docs/api.md)

---

## Multi-Agent Pipeline

The core intelligence lives in a **LangGraph StateGraph** with 15 agent nodes:

```
START → SupervisorAgent
         ├── RequirementAnalystAgent → FeatureInventoryAgent → END
         ├── BacklogCreationAgent → QAStoryAnalyzerAgent → ScenarioAgent
         ├── ScenarioAgent → HumanApprovalAgent (Gate 1)
         │        └── (rejected → END / approved → continue)
         ├── TestCaseAgent → EvaluationAgent → HumanApprovalAgent (Gate 2)
         │        └── (rejected → END / approved → continue)
         ├── PlaywrightAgent → (END / continue to execution)
         └── ExecutionAgent → ExecutionAnalysisAgent
                  → DefectManagementAgent → ReportAgent → END
```

Each agent reads `WorkflowState`, performs its task, and writes only its designated fields. The supervisor routes based on operation type (`INGEST`, `BACKLOG`, `GENERATE_SCENARIOS`, `GENERATE_TESTCASES`, `GENERATE_PLAYWRIGHT`, `EXECUTE`, `SYNC_USER_STORY`, `SYNC_BUG`).

Agent details: [docs/agents.md](docs/agents.md)

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=backend

# Run specific test file
pytest backend/tests/test_scenario_agent.py -v

# Run tests matching a keyword
pytest -k "playwright"
```

The test suite covers agents (with mock LLM responses via `mock_response=` parameter), services, API endpoints, workflows, and integration scenarios.

---

## Documentation

### Core Documentation
- [**Architecture**](docs/architecture.md) - Complete system architecture and design patterns
- [**Agents**](docs/agents.md) - Multi-agent pipeline and responsibilities
- [**Workflow**](docs/workflow.md) - LangGraph state machine and routing
- [**API Reference**](docs/api.md) - REST API endpoints and schemas

### Feature Documentation
- [**LangSmith Tracing**](docs/features/langsmith-tracing.md) - Unified observability and monitoring
- [**Professional Reporting**](docs/features/professional-reporting.md) - Enhanced HTML reports
- [**Naming Convention**](docs/features/naming-convention.md) - User Story and Test Case IDs
- [**Locator Fallback**](docs/features/locator-fallback.md) - Hierarchical locator resolution

### Guides
- [**Adactin Reference**](docs/guides/adactin-reference.md) - Test application guide
- [**Report Debugging**](docs/guides/report-debugging.md) - Troubleshooting reports
- [**Navigation Verification**](docs/guides/navigation-verification.md) - UI testing guide

### Implementation Reports
- [**Implementation Summary**](docs/implementation-reports/implementation-summary.md) - Overall status
- [**Professional Report Implementation**](docs/implementation-reports/professional-report-implementation.md) - Report engine details

**Full documentation index**: [docs/README.md](docs/README.md)

---

## Recent Updates (v1.3.0)

### LangSmith Tracing Integration ✨
- **Unified Observability**: All agents, LLM calls, and state transitions traced in one hierarchical view
- **Environment Configuration**: Automated setup in `backend/graph/workflow.py`
- **Dashboard Access**: View complete execution traces at https://smith.langchain.com
- **Captured Metrics**: Agent duration, LLM prompts/completions, token usage, errors
- **Documentation**: [LangSmith Tracing Guide](docs/features/langsmith-tracing.md)

### Jira HTML Report Attachments 🐛
- **Enhanced Traceability**: HTML execution reports automatically attached to JIRA bug tickets
- **Complete Context**: Screenshots + full HTML report provide comprehensive failure analysis
- **Seamless Integration**: Works alongside existing JIRA defect management workflow
- **Graceful Degradation**: Continues workflow if report file unavailable
- **Implementation**: Modified `backend/agents/jira_sync_agent.py` (lines 314-323)

---

## Testing

### Run Tests
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=backend

# Run specific test file
pytest backend/tests/test_scenario_agent.py -v

# Run LangSmith tracing test
python test_all_agents_trace.py
```

### Verify LangSmith Tracing
```bash
# Generate complete agent hierarchy trace
python test_all_agents_trace.py

# View trace in LangSmith dashboard:
# 1. Open https://smith.langchain.com
# 2. Select project "My Project"
# 3. Look for trace "Complete_Workflow_Test"
```

The test suite covers agents (with mock LLM responses), services, API endpoints, workflows, and integration scenarios.

---

## License

Proprietary. All rights reserved.
