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
    ExecutionAnalysisAgent (log analyser)                   │ │
      │  - Read report.json, trace.zip, page snapshot       │ │
      │  - Classify failure type with quoted evidence       │ │
      │  - Detect flakiness from the test's run history     │ │
      ▼                                                      │ │
    SelfHealingAgent (only after a failure, spec 0011)      │ │
      │  - Stage 1: heal a broken locator from the harvest  │ │
      │  - Stage 2: repair the script from the RCA          │ │
      │  - Rerun once per stage, at most 2 reruns           │ │
      │  - Put back any change whose rerun still fails      │ │
      ▼                                                      │ │
    BugReportingAgent (latest result per test case)         │ │
      │  - Draft a bug report per Product Bug failure       │ │
      │  - Reuse the open report for a repeat failure       │ │
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

## Grounded Bulk Script Validation

The bulk script action is available only when the selected project is marked as a safe test environment. That setting is off by default and can be changed in Project Settings by a user with project edit permission. Before each batch, the tester confirms that inspection and form submissions are safe for the target site.

The UI submits approved test case IDs to `POST /api/v1/projects/{project_id}/testcases/validate-scripts` with explicit safety confirmation. It polls the existing project test case list for per case validation state.

The backend inspects the selected project's target URL and gives bounded page evidence to script generation. The evidence includes observed controls and relevant visible text, but excludes field values and URL query strings. A form field's `label` holds only text Playwright's `getByLabel` can resolve (a linked or wrapping `<label>`, or `aria-label`); its `name` attribute is reported separately. When a page's caption is not tied to its field, as on pages whose `<label>` has no `for` attribute, the label is empty and the script must use the field's observed selector, which validation enforces. Form fields and buttons that are hidden at load are also reported, with `visible: false` (buttons under the form's `hidden_buttons`), so later steps of a multi-step form on one URL, such as a wizard, have observed selectors too. Hidden fields are ignored when deciding whether a page is a login page. A checkbox or radio with a `<label>` also gets a `click_selector` for that label (from the label's id, its `for` attribute, the input it wraps, or the input's `name` and `value`), kept only when it matches one element, plus its `value` attribute. Scripts toggle these options by clicking the label, because many forms cover the native input with a styled element that intercepts clicks; a data value is matched to an option by its label or value. The live recorder's element list includes the same option labels. Invalid login probes run only when an approved case explicitly supplies invalid username and password test data. Project credentials are not sent to the model.

A form whose steps are shown one at a time (sibling containers of the same tag and class that each hold a button, with exactly one visible) is reported with `steps`: each step's number, its name (from the tab or progress list, else its heading), whether it was visible at load, and the unique selectors of its `next_button` and `prev_button`. Every field, hidden button, and submit button of that form carries the `step` it sits on. The script prompt asks the model to finish each step's controls before clicking its next button, and `validate_step_order` in `backend/services/playwright_script_validation.py` flags a script that uses a control, or clicks a next button, of a step it already left. A data-driven script with such a finding is regenerated once. Only backward moves are flagged, and only when the evidence has exactly one stepped form.

After the listed URLs, reconnaissance starts again at the target URL and follows steps that name a link (a step beginning with click, select, choose, open, go to, navigate to, or tap whose text contains a visible link's exact name), up to 3 links. It clicks the link and inspects the page it leads to, recorded with `reached_by`. That page replaces a direct load of the same URL, because some applications set state when the link is clicked: the Tricentis quote form reads the vehicle type from a cookie that its menu link sets, and opened directly it shows and requires every vehicle's fields. Scripts likewise perform every navigation step the case lists, even when `BASE_URL` seems to show the destination already.

For a project marked as a safe test environment, each data-driven template is first recorded live with one dataset (`generate_data_driven_template` in `backend/agents/script_generator_agent.py`) and then written from that trace. Each recorded click or selection carries `element` facts: the element's text and its control's id, name, type, and value. A styled choice that is an empty label around a hidden input is therefore templated by its value attribute (`label:has(input[value='<marker>'])`), not by text it does not have. A template that leaves out a verified click whose selector names no test data value (such as a menu link before a form) is regenerated once. The `BASE_URL` fallback in every generated script is set to the project's target URL, because a deeper URL copied from the trace would skip the project's entry steps. Executions set `TARGET_URL` from the run's project configuration snapshot (spec 0001), so the fallback only matters for runs outside that path.

In a multi-step form or wizard, the next step's tab can open even when the application rejected the previous step's data, while that step's controls stay hidden. Generated scripts therefore assert that the first control of each new step is visible before using it, with a message naming both steps, so a rejected earlier value fails within 5 seconds with that message instead of timing out on the click. The runner's `expect` shim in `backend/playwrightt/lib/execution-engine.ts` supports Playwright's `expect(value, 'message')` form and puts the message before the assertion error.

Each generated script is compiled, checked for observed locators and an expected outcome assertion, then executed with Playwright. A failed script can receive one repair attempt using the failure and refreshed evidence. The latest validation state is stored separately from test execution status as `running`, `validated`, `failed`, or `unverified`. A script is ready only when its validation state is `validated`; failed and unverified scripts remain visible for tester review.

## Object Repository in the Workflow

The object repository (spec 0004, guide: [features/object-repository.md](features/object-repository.md)) touches five workflow stages:

1. **Reconnaissance**: when a safe test environment is inspected (script generation, grounded validation, or Scan now), each page's buttons, links, form fields, and navigation links go to `ObjectRepositoryService.ingest_recon`. That data is removed from the evidence first, so the prompt evidence is unchanged.
2. **Generation**: `PlaywrightAgent` lists the stored elements of every page the test case visits in an `OBJECT REPOSITORY` prompt section. The model locates them with `orx(page, '<page>.<element>')`. Unknown keys get one repair in the existing repair loops; if they remain, the validation diagnostic is `Unknown object repository keys: ...`. The live-verified generator, and data-driven template recording, take a different route. While recording, they match each acted-on element to a stored element by DOM node. After the script is written, they replace the matching `page.locator(...)` calls with `orx` keys (see [Live-verified scripts](features/object-repository.md#live-verified-scripts)).
3. **Read and dispatch**: stored scripts hold keys only. The helper and the current locators are injected when a script is returned by the API, sent to the workspace, built into a `QueueItem` by `ExecutionAgent` or the batch route, or validated and run by grounded validation.
4. **Run feedback**: `orx` logs one `[OBJREPO]` marker per lookup. Both execution paths hand the markers to `ExecutionService.apply_object_repository_markers`, which updates element health once per execution.
5. **Harvest**: while a test runs, each distinct path the page settles on is snapshotted once with the shared extractor. The workspace engine sends the snapshots as `harvest` on its `completed` webhook; the Python runner writes them to `harvest.json`, which `run_execution_and_stream` and the batch route read per run directory after the result is saved. Both go to `ExecutionService.apply_object_repository_harvest`, which redacts and ingests with the reconnaissance rules minus marking missing. Harvesting never changes a run's result.

## Failure Triage (Log Analyser)

Every failed or errored run is triaged by `LogAnalysisService`, on all three execution paths:

| Path | Where triage runs | Then |
|---|---|---|
| Single run (LangGraph) | `ExecutionAnalysisAgent` node | BugReportingAgent, ReportAgent |
| Batch run | `ExecutionService.start_batch_run`, per failed item, before saving | Bug report draft for a Product Bug; saved; Story sync |
| Workspace webhook | `ExecutionService.triage_saved_execution`, as a background task after the result is saved, so the webhook responds without waiting for the LLM | Triage fields saved onto the stored result |

How it works:
- **Inputs.** It reads `report.json`, `trace.zip` and `error-context.md` from the run folder (text only, redacted, size-capped), plus the test's last 10 runs.
- **Output.** It stores the category, root cause, failing step, quoted evidence, confidence and flaky score on the `ExecutionResult`.
- **Batch events.** A batch shows `Analysing failure logs for <test>...` and `Triage complete for <test>: <category>` events.
- **Never blocks a run.** A triage error never fails a run, a batch, or a webhook. An LLM failure saves the fallback category `Test Environment Issue`.
- **Bug reports.** After triage, a `Product Bug` gets a bug report draft (`BugReportService`) on every path. Jira bugs are filed only when a person submits the draft, or by project auto-submit. `sync_execution_finished` updates the Story but no longer creates bugs.

See [agents.md](agents.md#12-executionanalysisagent) for the rules.

---

## State Machine Design

### WorkflowState Schema

```python
class WorkflowState(BaseModel):
    project_id: UUID | None          # set by every state creator (spec 0006)
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
    # Retrieval stage (spec 0006), written only by KnowledgeRetrievalAgent
    retrieval_status: str | None     # "ok", "empty", or "skipped"
    retrieval_query: str | None
    retrieval_attempts: int
    retrieval_sources: list[str]
    retrieved_context: list[dict]    # {chunk_id, text, score, source_type, source_ref, title, graded}
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
    execution_analysis_agent=None, bug_reporting_agent=None,
    knowledge_retrieval_agents=None,  # {"requirements" | "scenarios" | "test_cases" | "analysis": agent}
    self_healing_agent=None,          # spec 0011
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
"bug_reporting_agent"     → bug_reporting_agent
"self_healing_agent"      → self_healing_agent
"retrieval_requirements"  → retrieval_requirements
"retrieval_scenarios"     → retrieval_scenarios
"retrieval_test_cases"    → retrieval_test_cases
"retrieval_analysis"      → retrieval_analysis
"end"                     → END
```

Fixed edges (always execute in sequence):
- `scenario_agent → human_approval_agent_1`
- `test_case_agent → evaluation_agent → human_approval_agent_2`
- `bug_reporting_agent → report_agent → END`
- each retrieval node to its consumer: `retrieval_requirements → requirement_analyst_agent`, `retrieval_scenarios → scenario_agent`, `retrieval_test_cases → test_case_agent`, `retrieval_analysis → execution_analysis_agent`

### Self healing (spec 0011)

`execution_analysis_agent` used to have a fixed edge to `bug_reporting_agent`. Two routers replace it:

- `route_after_execution_analysis` goes to `self_healing_agent` when `state.healing_enabled` is true and either an applied heal waits for its rerun check, or `heal_attempts < object_repository.healing.max_attempts_per_run` and the latest failed or errored result (not a `Test Environment Issue`) has an `[OBJREPO]` marker with `ok: false`. Otherwise it goes to `bug_reporting_agent`.
- It also goes to `self_healing_agent` for stage 2, script repair (amendment, AC-18), when the latest result failed with no `ok: false` marker and the log analyser's RCA is `Flaky Test / Automation Issue` with confidence of at least `object_repository.healing.script_repair.min_rca_confidence` (0.6). The case must have a script and no `test_data_sets`, and `script_repair_attempts` must be under `script_repair.max_attempts` (1). It goes there too while an applied script fix waits for its rerun check. A run where every result passed never goes to self healing.
- `route_after_self_healing` goes back to `execution_agent` when `heal_rerun_test_case_ids` is set, else to `bug_reporting_agent`.

A heal rerun runs only the healed test cases, once, with `RecoveryManager` retries off and the first run's batch id (so the saved login session is reused). It appends a second result with `heal_rerun_of` pointing at the failed one. Every reader after that (BugReportingAgent, ReportAgent, `WorkflowService`, the LangSmith tracer) uses the latest result per test case (`WorkflowState.latest_results()`), and run history leaves out the replaced attempt. `WorkflowService` sets `healing_enabled` from `object_repository.healing.enabled` and the project's `self_healing_enabled`, and saves every result, giving the caller's `execution_id` to the latest. The batch and webhook paths don't heal yet.

**Two stages.** When the stage 1 rerun still fails but the healed key resolved, and the RCA of that rerun is an automation issue, the heal waits (`verified: "pending_script_repair"`) and stage 2 runs in the same visit:
- `ScriptRepairService` regenerates the script through `PlaywrightAgent` with `repair_feedback`. The feedback is built from the RCA, with run text wrapped as untrusted data and redacted. The page evidence is the run's harvest, and the prompt lists the healed keys.
- The new script is applied only when the validator, the unknown key check, and the assertion count gate all pass. It is then rerun once.
- A passing rerun keeps the script fix and the waiting heal. A failing one restores the old script and puts both back as suggestions.

So a test case runs at most three times: the first run, stage 1, and stage 2. A `Product Bug` or `Test Environment Issue` RCA never changes a script.

### Retrieval stage (spec 0006)

When `RAG_WORKFLOW_RETRIEVAL_ENABLED` is true, `SupervisorAgent._via_retrieval(target)` sends each route that would reach `requirement_analyst_agent`, `scenario_agent`, `test_case_agent`, or `execution_analysis_agent` to that consumer's retrieval node first. It is used in `route_start` (INGEST, GENERATE_SCENARIOS, the no operation fallback, GENERATE_TESTCASES), `route_after_human_approval_1`, and two routers that replaced former fixed edges: `route_after_qa_story_analyzer` (to `scenario_agent`) and `route_after_execution` (to `execution_analysis_agent`). The flag is read on every route call, so the module level `graph` built at import time follows a settings change. With the flag off, the order is the same as before and the retrieval nodes are never reached.

Every `WorkflowState` creator sets `project_id` (`run_workflow(requirement, project_id=...)`, `WorkflowService`, the Jira sync routes, and `GroundedPlaywrightScriptService`). Without it the retrieval stage skips.

Inside each retrieval node, every search, grade, and rewrite is a LangSmith child span when tracing is on (see [agents.md](agents.md), KnowledgeRetrievalAgent). After `test_case_agent`, `evaluation_agent` reuses the `retrieval_test_cases` context to score each test case's groundedness; it is reported on the test case and never changes the evaluation outcome.

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
