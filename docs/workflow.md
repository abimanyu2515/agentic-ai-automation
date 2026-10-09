# Workflow

The platform orchestrates AI-driven test automation through a **LangGraph state machine**. This document explains each workflow path, how they're triggered, and what happens at each step.

---

## Workflow Operations

The SupervisorAgent routes based on the `operation` field in the workflow config. There are **8 operations**:

| Operation | Entry Agent | Purpose |
|---|---|---|
| `INGEST` | RequirementAnalystAgent | Enrich raw requirement with LLM |
| `BACKLOG` | BacklogCreationAgent | Generate agile backlog from scenarios |
| `GENERATE_SCENARIOS` | ScenarioAgent | Create test scenarios |
| `GENERATE_TESTCASES` | TestCaseAgent | Create detailed test cases |
| `GENERATE_PLAYWRIGHT` | PlaywrightAgent | Generate automation scripts |
| `EXECUTE` | ExecutionAgent | Run automated tests |
| `SYNC_USER_STORY` | JiraSyncAgent | Sync scenarios to JIRA as Stories |
| `SYNC_BUG` | JiraSyncAgent | Sync failures to JIRA as Bugs |

---

## Pipeline Diagrams

### Full End-to-End Pipeline

```
START
 │
 ▼
SupervisorAgent ──(reads operation)──►
 │
 ├── INGEST ──────────────────────────────────────────────┐
 │    │                                                    │
 │    ▼                                                    │
 │  RequirementAnalystAgent                                │
 │    │  - Parse requirement text                          │
 │    │  - Extract FR / NFR / business rules               │
 │    │  - Identify risks and assumptions                  │
 │    ▼                                                    │
 │  FeatureInventoryAgent  (if RAG enabled)                │
 │    │  - Map to existing features                        │
 │    ▼                                                    │
 │  END  ◄── (requirement enriched and stored)             │
 │                                                         │
 ├── BACKLOG ─────────────────────────────────────────────┐ │
 │    │                                                    │ │
 │    ▼                                                    │ │
 │  BacklogCreationAgent                                   │ │
 │    │  - Generate user stories from requirement          │ │
 │    ▼                                                    │ │
 │  QAStoryAnalyzerAgent  (optional)                       │ │
 │    │  - Analyze story testability                      │ │
 │    ▼                                                    │ │
 │  ScenarioAgent  ──► (continues to scenario path)       │ │
 │                                                         │ │
 ├── GENERATE_SCENARIOS ──────────────────────────────────┘ │
 │    │                                                      │
 │    ▼                                                      │
 │  ScenarioAgent                                            │
 │    │  - Call LLM with requirement context                 │
 │    │  - Generate N scenarios (configurable count)         │
 │    │  - Each with: name, description, priority, confidence│
 │    ▼                                                      │
 │  HumanApprovalAgent (Gate 1)                              │
 │    │                                                      │
 │    ├── If rejected ──► END                                │
 │    │                                                      │
 │    └── If approved ──► (continues to test case path)     │
 │                                                           │
 ├── GENERATE_TESTCASES ───────────────────────────────────┐ │
 │    │                                                      │ │
 │    ▼                                                      │ │
 │  TestCaseAgent                                            │ │
 │    │  - For each approved scenario:                      │ │
 │    │  - Generate test cases with:                        │ │
 │    │    • Title, preconditions, steps                    │ │
 │    │    • Expected result, priority                      │ │
 │    │    • Confidence score                               │ │
 │    │    • Execution type classification                  │ │
 │    ▼                                                      │ │
 │  EvaluationAgent                                         │ │
 │    │  - Check for duplicates (Jaccard similarity)        │ │
 │    │  - Score relevance to parent scenario               │ │
 │    │  - Set evaluation_status: APPROVED/NEEDS_REVIEW/    │ │
 │    │    DUPLICATE                                        │ │
 │    │  - Sort by confidence descending                    │ │
 │    ▼                                                      │ │
 │  HumanApprovalAgent (Gate 2)                              │ │
 │    │                                                      │ │
 │    ├── If rejected ──► END                                │ │
 │    │                                                      │ │
 │    └── If approved ──► (continues to script path)       │ │
 │                                                           │ │
 ├── GENERATE_PLAYWRIGHT ─────────────────────────────────┘ │
 │    │                                                      │
 │    ▼                                                      │
 │  AutomationOrchestratorAgent                              │
 │    │  - Classify test case as:                            │
 │    │    • UI Automated                                    │
 │    │    • API Automated                                   │
 │    │    • Manual Testing                                  │
 │    ▼                                                      │
 │  PlaywrightAgent                                         │
 │    │  - Generate Playwright TypeScript script             │
 │    │  - Include locators, assertions, navigation         │
 │    │  - Handle timeouts, waits, screenshots              │
 │    ▼                                                      │
 │  ──► (if human_review_enabled → Gate check)             │
 │    │                                                      │
 │    └──► END or (continue to execution)                   │
 │                                                           │
 ├── EXECUTE ──────────────────────────────────────────────┐ │
      │                                                      │ │
      ▼                                                      │ │
    ExecutionAgent                                          │ │
      │  - Launch Playwright via npx                         │ │
      │  - Stream real-time logs via SSE                     │ │
      │  - Capture screenshots, video, traces               │ │
      ▼                                                      │ │
    ExecutionAnalysisAgent                                  │ │
      │  - Analyze failure root cause                       │ │
      │  - Classify failure type                            │ │
      │  - Detect flakiness                                 │ │
      ▼                                                      │ │
    DefectManagementAgent                                   │ │
      │  - Check JIRA for duplicates                        │ │
      │  - Create/update bug tickets                        │ │
      ▼                                                      │ │
    ReportAgent                                             │ │
      │  - Compile JUnit XML report                         │ │
      │  - Generate JSON summary                            │ │
      ▼                                                      │ │
    END  ◄── (execution complete, results stored)          │ │
                                                             │ │
 └── SYNC_USER_STORY / SYNC_BUG ─────────────────────────┘
      │
      ▼
    JiraSyncAgent
      │  - Create/update JIRA issues
      │  - Sync status bi-directionally
      ▼
    END
```

---

## State Machine Design

### WorkflowState Schema

```python
class WorkflowState(BaseModel):
    requirement: Requirement | None
    generated_scenarios: list[Scenario]
    selected_scenario_ids: list[UUID]
    generated_test_cases: list[TestCase]
    pending_approval_ids: list[UUID]
    human_approved_test_case_ids: list[UUID]
    execution_results: list[ExecutionResult]
    execution_report: ExecutionReport | None
    logs: list[str]
    user_stories: list[dict]
    testing_context: dict | None
```

### Design Principles

1. **Selection is id-based, not flag-based**
   - `selected_scenario_ids` tracks which scenarios are chosen
   - This keeps "what's selected" answerable without scanning for mutated flags

2. **AI evaluation and human approval are independent**
   - `EvaluationStatus` (APPROVED/NEEDS_REVIEW/DUPLICATE) is EvaluationAgent's permanent record
   - `human_approved_test_case_ids` is a separate list — human approval adds a second decision on top, never rewrites history

3. **Execution results are a list, not a field**
   - One test case can be run multiple times (retries, re-runs after fixes)
   - Each run is an independent `ExecutionResult`, not an overwritten field

4. **Logs accumulate across all agents**
   - Every agent appends start/completion logs with timestamps
   - Provides full audit trail of every workflow execution

---

## Human Review Flow

When `human_review_enabled = true`, the pipeline pauses at two gates:

### Gate 1: Scenario Approval

```
ScenarioAgent generates scenarios
        │
        ▼
HumanApprovalAgent checks mode
        │
        ├── auto-approve mode → all scenarios approved → continue
        │
        └── human review mode → scenarios marked pending
                │
                ▼
        API: PUT scenarios/{id} {approved: true/false}
                │
                ▼
        All approved → TestCaseAgent
        Some rejected → continue with approved only
        All rejected → END
```

### Gate 2: Test Case Approval

```
EvaluationAgent scores test cases
        │
        ▼
HumanApprovalAgent checks mode
        │
        ├── auto-approve → all above threshold → continue
        │
        └── human review → pending approval list
                │
                ▼
        API: PUT testcases/{id} {evaluation_status: approved/rejected}
                │
                ▼
        Approved TCs → PlaywrightAgent
        Rejected TCs → excluded from script generation
```

---

## Graph Construction

The graph is built by `build_graph()` in `backend/graph/workflow.py`:

```python
def build_graph(
    scenario_agent=None, test_case_agent=None,
    evaluation_agent=None, human_approval_agent=None,
    playwright_agent=None, execution_agent=None,
    report_agent=None, requirement_analyst_agent=None,
    feature_inventory_agent=None, backlog_creation_agent=None,
    jira_sync_agent=None, qa_story_analyzer_agent=None,
    execution_analysis_agent=None, defect_management_agent=None,
):
```

All parameters are optional — defaults create production agents. This enables **dependency injection** for testing:

```python
# Testing: inject mock agents
mock_scenario = MockScenarioAgent()
graph = build_graph(scenario_agent=mock_scenario)

# Production: all real agents
graph = build_graph()
```

---

## Edge Routing Logic

```
SupervisorAgent routing_map:

"scenario_agent"          → scenario_agent
"human_approval_agent_1"  → human_approval_agent_1
"test_case_agent"         → test_case_agent
"evaluation_agent"        → evaluation_agent
"human_approval_agent_2"  → human_approval_agent_2
"playwright_agent"        → playwright_agent
"execution_agent"         → execution_agent
"report_agent"            → report_agent
"requirement_analyst"     → requirement_analyst_agent
"feature_inventory"       → feature_inventory_agent
"backlog_creation"        → backlog_creation_agent
"qa_story_analyzer"       → qa_story_analyzer_agent
"jira_sync_agent"         → jira_sync_agent
"execution_analysis"      → execution_analysis_agent
"defect_management"       → defect_management_agent
"end"                     → END
```

Fixed edges (always execute in sequence):
- `scenario_agent → human_approval_agent_1`
- `test_case_agent → evaluation_agent → human_approval_agent_2`
- `execution_agent → execution_analysis_agent → defect_management_agent → report_agent → END`

---

## Execution Stream Protocol

For the `EXECUTE` operation, the system uses a streaming protocol:

```
1. Client POSTs to /api/v1/projects/{id}/executions
2. Server queues the execution
3. ExecutionAgent runs Playwright in background
4. Events streamed via in-memory buffer (active_execution_events)
5. Client polls or subscribes to event stream
6. Each event contains: status, timeline, log, screenshot, artifact
7. Final event signals completion with full ExecutionResult
```

### Event States

| State | Meaning |
|---|---|
| `Queued` | Waiting in runner pipeline |
| `Preparing Environment` | Initializing Playwright process |
| `Running` | Test executing with real-time log streaming |
| `Execution Analysis` | Analyzing failure (post-run) |
| `Jira Sync` | Checking/creating JIRA tickets |
| `Reporting` | Compiling reports |
| `Completed` | Execution finished (success or failure) |

---

## Testing the Workflow

```python
from backend.graph.workflow import run_workflow, build_graph
from backend.models.requirement import Requirement

# Real execution
req = Requirement(title="Login", description="User should be able to login")
result = run_workflow(req)

# With mocked agents
from unittest.mock import Mock, MagicMock
mock_scenario = MagicMock()
mock_graph = build_graph(scenario_agent=mock_scenario)
result = run_workflow(req, graph_instance=mock_graph)
```

The `run_workflow()` function is the primary entry point and is used by both production API calls and integration tests.
