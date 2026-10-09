# Architecture

## System Overview

The platform follows a **layered architecture** with clear separation of concerns:

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Frontend (SPA)                                │
│  index.html  ·  app.js  ·  api.js  ·  components.js  ·  styles.css  │
│  Chart.js (dashboards)  ·  Monaco Editor (script editing)            │
└────────────────────────────────┬─────────────────────────────────────┘
                                 │ HTTP/JSON (REST API)
                                 │ JWT Bearer Token Auth
┌────────────────────────────────▼─────────────────────────────────────┐
│                     FastAPI Application (main.py)                     │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │                   API Layer (REST Endpoints)                    │   │
│  │  Projects · Requirements · Scenarios · Test Cases · Executions  │   │
│  │  Reports · Settings · Auth · Dashboard Metrics · JIRA Import    │   │
│  └──────────────────────────┬─────────────────────────────────────┘   │
│                             │                                         │
│  ┌──────────────────────────▼─────────────────────────────────────┐   │
│  │                    Service Layer (Business Logic)               │   │
│  │  ┌────────────┐ ┌──────────────┐ ┌──────────┐ ┌────────────┐   │   │
│  │  │ProjectSvc  │ │WorkflowSvc   │ │AuthSvc   │ │ReportSvc   │   │   │
│  │  │VaultSvc    │ │ExecutionSvc  │ │JiraSvc   │ │AuditSvc    │   │   │
│  │  │RAGService  │ │RecoveryMgr   │ │BatchMgr   │ │ArtifactMgr │   │   │
│  │  └─────┬──────┘ └──────┬───────┘ └──────────┘ └────────────┘   │   │
│  └───────┬┴───────────────┼───────────────────────────────────────┘   │
│          │                │                                           │
│  ┌───────▼────────────────▼───────────────────────────────────────┐   │
│  │              LangGraph Agent Layer (StateGraph)                 │   │
│  │                                                                 │   │
│  │  SupervisorAgent (routing)                                      │   │
│  │       │                                                         │   │
│  │       ├── RequirementAnalystAgent  ─── FeatureInventoryAgent    │   │
│  │       ├── BacklogCreationAgent     ─── QAStoryAnalyzerAgent     │   │
│  │       ├── ScenarioAgent            ─── HumanApprovalAgent      │   │
│  │       ├── TestCaseAgent            ─── EvaluationAgent          │   │
│  │       ├── PlaywrightAgent                                      │   │
│  │       ├── ExecutionAgent           ─── ExecutionAnalysisAgent   │   │
│  │       ├── DefectManagementAgent    ─── ReportAgent              │   │
│  │       └── JiraSyncAgent                                        │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                             │                                         │
└─────────────────────────────┼─────────────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────────────┐
│                   Data / Persistence Layer                             │
│                                                                       │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  ProjectRepository│  │  PostgresRepo   │  │  SQLAlchemy ORM     │  │
│  │  (JSON file)     │  │  (optional)     │  │  Models + Alembic   │  │
│  └─────────────────┘  └──────────────────┘  └──────────────────────┘  │
│                                                                       │
│  ┌─────────────────┐  ┌──────────────────┐                            │
│  │  Vault JSON     │  │  project_store   │                            │
│  │  (encrypted)    │  │  (JSON DB)       │                            │
│  └─────────────────┘  └──────────────────┘                            │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Layer Descriptions

### 1. Frontend Layer

A single-page application served as static files. No build step — vanilla JS with Chart.js for dashboards and Monaco Editor for script authoring.

- **State management**: In-memory JS objects, no framework
- **API communication**: `api.js` wraps `fetch()` with JWT token handling
- **View switching**: Hash-based routing via `app.js`
- **Components**: `components.js` provides reusable table, modal, card builders

### 2. API Layer

FastAPI application defined in `backend/main.py` (~1300 lines) with:

- **CORS middleware** — open for development
- **Static file mounting** — serves `frontend/` at root
- **JWT authentication** — middleware checks protected routes
- **RBAC enforcement** — `has_permission()` dependency for admin/manager/engineer/viewer roles
- **Background tasks** — long-running file parsing and execution streams

All routes follow the pattern: validate input → call service → serialize response.

### 3. Service Layer

Domain services encapsulate business logic. Key services:

| Service | Responsibility |
|---|---|
| `ProjectService` | Project, requirement, scenario, test case CRUD |
| `WorkflowService` | LangGraph orchestration for all generation/execution operations |
| `AuthService` | JWT creation, password hashing, token refresh |
| `VaultService` | Encrypted credential storage/retrieval |
| `JiraService` | JIRA API integration (stories, bugs, status sync) |
| `ExecutionService` | Test execution orchestration and streaming |
| `ReportService` | JUnit XML report compilation |
| `RAGService` | RAGFlow document upload and retrieval |
| `AuditService` | Audit log writing |
| `RecoveryManager` | Crash recovery for interrupted executions |
| `BatchQueueManager` | Queue management for batch test runs |

### 4. Agent Layer (LangGraph)

The core AI orchestration. A `StateGraph` where each node is an agent implementing `BaseAgent.run(state) -> state`. 

Design principles:
- **State-only communication** — agents never call each other directly
- **Write-only ownership** — each agent modifies only its designated state fields
- **Supervisor routing** — Central agent decides "who runs next" via conditional edges
- **Dependency injection** — `build_graph()` accepts mock agents for testing

### 5. Data Layer

Abstract `ProjectRepository` interface with two implementations:

- **JSON file repository** (`project_repository.py`): Stores all data in `backend/database/project_store.json`. Transactions are atomic per file write.
- **PostgreSQL repository** (`postgres_project_repository.py`): Full SQLAlchemy ORM with connection pooling.

The vault (`backend/database/vault.json`) stores credentials encrypted with `cryptography` Fernet.

---

## Configuration Architecture

```
                     ┌──────────────────────┐
                     │  .env (secrets)       │
                     │  LLM_API_KEY          │
                     │  LANGSMITH_API_KEY    │
                     │  DATABASE_URL         │
                     └──────────┬───────────┘
                                │ overrides
┌───────────────────┐  ┌───────▼────────────┐
│ default_config    │  │  override.yaml     │
│ .yaml (committed) │──┤ (runtime edits)    │
│                   │  │                    │
│ - LLM defaults    │  │ UI settings saves  │
│ - Workflow mode   │  │ write here         │
│ - Thresholds      │  │                    │
└───────────────────┘  └────────────────────┘
         │                      │
         └──────────┬───────────┘
                    ▼
          ┌──────────────────┐
          │  get_settings()  │
          │  (cached)        │
          └──────────────────┘
```

Precedence: `.env` > `override.yaml` > `default_config.yaml`

---

## Data Flow: Request Through Pipeline

```
Client Request
    │
    ▼
FastAPI Route (e.g., POST /generate-scenarios)
    │
    ▼
WorkflowService.generate_scenarios()
    │
    ├── Fetch requirement from repository
    ├── Build LangGraph with ScenarioAgent
    ├── Invoke graph with WorkflowState
    │       │
    │       ▼
    │   StateGraph
    │   START → SupervisorAgent
    │        → ScenarioAgent (LLM generates scenarios)
    │        → HumanApprovalAgent (check review mode)
    │        → END
    │
    ├── Persist generated scenarios to repository
    └── Return list of Scenario objects
    │
    ▼
FastAPI serializes to ScenarioResponse DTO
    │
    ▼
Client receives JSON response
```

---

## Security Architecture

- **Password storage**: bcrypt hashing via passlib
- **JWT tokens**: python-jose with HS256, configurable expiry
- **Credential vault**: AES encryption via `cryptography.fernet`
- **RBAC**: 4 roles (admin, manager, engineer, viewer) with granular permissions per entity+action
- **Audit trail**: All create/update/delete operations logged with user, timestamp, IP

---

## Scalability Considerations

- **Stateless API**: All state lives in the repository or LangGraph's in-memory execution
- **Async execution**: Long-running Playwright tests use background tasks with SSE-like polling
- **Repository swap**: JSON → PostgreSQL migration requires no code changes beyond config
- **Agent isolation**: Each agent can be independently scaled or replaced
- **Multi-provider LLM**: Provider switch is a config change, no code modification needed

---

## Agent Architecture - Complete Flow

### Agent Hierarchy (16 Specialized Agents)

```
START
  ↓
SupervisorAgent (Traffic Controller)
  ↓
├─ RequirementAnalystAgent (Requirement Ingestion)
│   ↓
│   FeatureInventoryAgent (Optional RAG/Feature Mapping)
│
├─ BacklogCreationAgent (Agile Backlog Generation)
│   ↓
│   QAStoryAnalyzerAgent (Story Analysis)
│   ↓
│   ScenarioAgent (Test Scenario Generation)
│   ↓
│   HumanApprovalAgent_1 (Scenario Approval Gate)
│   ↓
│   TestCaseAgent (Detailed Test Case Generation)
│   ↓
│   EvaluationAgent (Test Case Quality Assessment)
│   ↓
│   HumanApprovalAgent_2 (Test Case Approval Gate)
│   ↓
│   QAAutomationOrchestratorAgent (Automation Path Classification)
│   ↓
│   PlaywrightAgent (Script Generation)
│   ↓
│   ExecutionAgent (Test Execution)
│   ↓
│   ExecutionAnalysisAgent (Failure Triage & Root Cause)
│   ↓
│   DefectManagementAgent (JIRA Bug Lifecycle)
│   ↓
│   ReportAgent (Report Generation)
│   ↓
END
```

### Operation Types & Agent Routing

#### 1. INGEST Operation
```
SupervisorAgent → RequirementAnalystAgent → FeatureInventoryAgent → END
```
- Parses requirements from PDF/Word/Excel/JIRA
- Enriches with LLM analysis
- Optional RAG-based feature mapping

#### 2. BACKLOG Operation
```
SupervisorAgent → BacklogCreationAgent → QAStoryAnalyzerAgent → ScenarioAgent → HumanApprovalAgent_1 → END
```
- Generates agile backlog items from requirements
- Analyzes QA stories
- Creates test scenarios
- Requires human approval

#### 3. GENERATE_SCENARIOS Operation
```
SupervisorAgent → ScenarioAgent → HumanApprovalAgent_1 → END
```
- Direct scenario generation from requirements
- Human approval gate before proceeding

#### 4. GENERATE_TESTCASES Operation
```
SupervisorAgent → TestCaseAgent → EvaluationAgent → HumanApprovalAgent_2 → END
```
- Converts approved scenarios to detailed test cases
- Evaluates test case quality
- Human approval required for automation

#### 5. GENERATE_PLAYWRIGHT Operation
```
SupervisorAgent → QAAutomationOrchestratorAgent → PlaywrightAgent → END
```
- Classifies automation pathway (Manual vs Automated)
- Generates Playwright TypeScript scripts
- Only runs for approved test cases

#### 6. EXECUTE Operation
```
SupervisorAgent → ExecutionAgent → ExecutionAnalysisAgent → DefectManagementAgent → ReportAgent → END
```
- Runs Playwright tests
- Analyzes failures (triages: Product Bug vs Environment vs Flaky)
- Manages JIRA bug lifecycle
- Generates execution reports

### Key Agent Responsibilities

#### SupervisorAgent
- Traffic controller that routes to appropriate agents
- Makes routing decisions based on operation type
- Handles conditional logic and state transitions

#### RequirementAnalystAgent
- Parses and enriches requirement documents
- Extracts acceptance criteria
- Identifies testable requirements

#### ScenarioAgent
- Generates test scenarios from requirements
- Covers happy path and edge cases
- Produces scenario descriptions and preconditions

#### TestCaseAgent
- Converts scenarios to detailed test cases
- Generates step-by-step instructions
- Defines expected results

#### EvaluationAgent
- Evaluates test case quality
- Checks for completeness and clarity
- Rates confidence and priority

#### HumanApprovalAgent
- Approval gate for scenarios and test cases
- Enforces human-in-the-loop validation
- Prevents low-quality automation

#### PlaywrightAgent
- Generates Playwright TypeScript scripts
- Uses site-specific selectors and best practices
- Handles different automation frameworks

#### ExecutionAgent
- Runs Playwright tests in isolated environment
- Captures screenshots, videos, traces
- Streams real-time execution logs
- Handles retries for flaky tests

#### ExecutionAnalysisAgent
- Triages failures into categories:
  - **Product Bug**: Actual application defect
  - **Environment Issue**: Infrastructure/network problem
  - **Flaky Test**: Intermittent test failure
- Determines root cause
- Only Product Bugs proceed to defect management

#### DefectManagementAgent
- Uses LLM to generate semantic JQL queries for duplicate detection
- Searches JIRA for existing bugs
- Links to existing bugs if duplicates found
- Creates new JIRA bug tickets if no duplicates
- Uploads screenshots as attachments
- Updates test cases with JIRA linkage

#### ReportAgent
- Generates execution reports (HTML/PDF/JUnit XML)
- Creates professional test reports
- Compiles metrics and analytics

### Data Flow Architecture

#### State Management
- `WorkflowState` object carries data through agent chain
- Contains: requirements, scenarios, test cases, execution results
- Each agent reads and updates state immutably

#### LLM Integration
- Centralized `LLMService` for all AI operations
- Structured generation with Pydantic models
- Supports multiple LLM providers

#### External Integrations
- **JIRA**: Bug tracking and user story sync
- **Playwright**: Test execution framework
- **LangSmith**: Agent observability and tracing

### Key Design Patterns

#### 1. Agent Pattern
- Each agent extends `BaseAgent`
- Implements `run(state: WorkflowState) -> WorkflowState`
- Single responsibility per agent

#### 2. State Machine Pattern
- LangGraph manages state transitions
- Conditional routing based on business logic
- Observable and debuggable

#### 3. Dependency Injection
- Agents can be mocked for testing
- Services injected via constructor
- Enables unit testing without external dependencies

#### 4. Human-in-the-Loop
- Approval gates prevent automation of low-quality tests
- Human validation at critical decision points
- Balances automation speed with quality

---

## Recent Enhancements (v1.3.0)

### LangSmith Tracing Integration

**Purpose**: Unified observability for all agent executions, LLM calls, and workflow decisions.

**Architecture**:
```
backend/graph/workflow.py (initialization)
  ↓
  ├─ Configure os.environ variables BEFORE importing @traceable
  │  (LANGSMITH_TRACING, LANGSMITH_API_KEY, LANGSMITH_PROJECT)
  │
  ├─ Import langsmith.traceable decorator
  │
  └─ Wrap each agent node with @traceable(name="AgentName")
     ↓
     All agent executions, LLM calls, and state transitions
     are automatically traced to LangSmith dashboard
```

**Key Components**:
- **Environment Setup**: `backend/graph/workflow.py` exports LangSmith environment variables to `os.environ` before importing the `@traceable` decorator
- **Agent Tracing**: Every agent node is wrapped with `@traceable(name="AgentName", project_name=project_name)`
- **LLM Auto-Tracing**: LangChain/LangGraph automatically traces all LLM calls with prompts, completions, and token usage
- **Hierarchical Traces**: All agents appear as child spans under the root workflow trace

**Trace Information Captured**:
- Agent execution duration
- Input/output state
- LLM prompts and completions
- Token usage metrics
- Error stack traces
- Requirement ID and metadata

**Configuration** (`.env`):
```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your_api_key>
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=My Project
```

**Dashboard Access**: https://smith.langchain.com → Select project → View hierarchical traces

### Jira HTML Report Attachments

**Purpose**: Attach execution HTML reports directly to JIRA bug tickets for complete traceability.

**Architecture**:
```
DefectManagementAgent
  ↓
  ├─ Sync failures as JIRA bugs
  │  ↓
  │  ├─ Create/Link JIRA ticket
  │  │
  │  ├─ Attach screenshots (existing)
  │  │
  │  └─ Attach HTML report (NEW)
  │     ↓
  │     ├─ Locate report: backend/playwrightt/public/artifacts/{execution_id}/report.html
  │     ├─ Upload via JiraService.upload_attachment()
  │     └─ Filename: execution_report_{execution_id}.html
```

**Key Components**:
- **Modified Agent**: `backend/agents/jira_sync_agent.py` (lines 314-323)
- **Report Location**: Execution reports stored in `backend/playwrightt/public/artifacts/{execution_id}/report.html`
- **Upload Method**: Uses existing `JiraService.upload_attachment()` infrastructure
- **Error Handling**: Graceful failure if report file missing (logs warning, doesn't break workflow)

**Implementation**:
```python
# In JiraSyncAgent._sync_failures_as_bugs()
report_path = Path(f"backend/playwrightt/public/artifacts/{execution_id}/report.html")
if report_path.exists():
    self.jira_service.upload_attachment(
        issue_key=jira_issue.key,
        file_path=str(report_path),
        filename=f"execution_report_{execution_id}.html"
    )
```

**Benefits**:
- Complete execution context attached to bug ticket
- QA team can review full report without accessing main platform
- Screenshots + HTML report provide comprehensive failure analysis
- Seamless integration with existing JIRA workflow

---

## Testing Strategy

### Unit Testing
- Mock LLM responses via `BaseAgent(mock_response=...)`
- Test each agent in isolation
- Verify state transformations

### Integration Testing
- Mock external services (JIRA, RAG)
- Test agent chains and routing
- Verify end-to-end workflows

### Tracing Verification
- Test script: `test_all_agents_trace.py`
- Generates unified trace with all 8 core agents
- Verifies LangSmith integration
- Confirms hierarchical trace structure

### Report Attachment Testing
- Verify HTML report generation
- Test JIRA attachment upload
- Confirm graceful failure handling
