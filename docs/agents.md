# Agents

The platform uses **15 specialized AI agents** orchestrated by a LangGraph `StateGraph`. Each agent implements `BaseAgent` and follows a strict contract: read from `WorkflowState`, process, write only its designated fields, return the updated state.

---

## Base Contract

```python
class BaseAgent(ABC):
    def run(self, state: WorkflowState) -> WorkflowState: ...
```

All agents:
- MUST validate preconditions before processing
- MUST log start/completion to `state.logs`
- SHOULD only modify fields within their responsibility domain
- MUST NOT modify other agents' output fields
- RAISE `ValueError` if preconditions are not met

---

## Agent Reference

### 1. SupervisorAgent

**File**: `backend/agents/supervisor_agent.py`

The decision engine. Reads `operation` from the workflow config and routes to the correct first agent.

**Routing functions:**

| Function | From Node | Destinations |
|---|---|---|
| `route_start` | START | Any agent based on operation |
| `route_after_requirement_analyst` | RequirementAnalystAgent | FeatureInventoryAgent or END |
| `route_after_backlog_creation` | BacklogCreationAgent | QAStoryAnalyzerAgent or ScenarioAgent |
| `route_after_human_approval_1` | HumanApprovalAgent (Gate 1) | TestCaseAgent or END |
| `route_after_human_approval_2` | HumanApprovalAgent (Gate 2) | PlaywrightAgent or END |
| `route_after_playwright` | PlaywrightAgent | ExecutionAgent or END |

**Framework integration**: None — pure routing logic.

---

### 2. RequirementAnalystAgent

**File**: `backend/agents/requirement_analyst_agent.py`
**Prompt**: `backend/prompts/agents/requirement_analyst/`

Ingests raw requirement text and enriches it via LLM. Extracts:
- Functional requirements
- Non-functional requirements
- Business rules
- Acceptance criteria
- Risks and assumptions

**State writes**: `state.requirement` (enriched with extracted fields)

**Operation**: `INGEST`

---

### 3. FeatureInventoryAgent

**File**: `backend/agents/feature_inventory_agent.py`
**Prompt**: `backend/prompts/agents/feature_inventory/`

Optionally maps requirements to existing feature inventory if RAG is enabled. Helps avoid duplicate scenario generation.

**State writes**: `state.requirement.feature_mapping`

**Operation**: `INGEST` (optional second stage)

---

### 4. BacklogCreationAgent

**File**: `backend/agents/backlog_creation_agent.py`
**Prompt**: `backend/prompts/agents/backlog_creation/`

Generates agile backlog items (user stories, tasks) from requirements. Populates `state.user_stories` with structured backlog entries.

**State writes**: `state.user_stories`, `state.requirement.backlog`

**Operation**: `BACKLOG`

---

### 5. QAStoryAnalyzerAgent

**File**: `backend/agents/qa_story_analyzer_agent.py`
**Prompt**: `backend/prompts/agents/qa_story_analyzer/`

Analyzes user stories from the backlog for testability, clarity, and completeness. Feeds analysis into the scenario generation phase.

**State writes**: `state.testing_context`

**Operation**: `BACKLOG` (optional sub-step)

---

### 6. ScenarioAgent

**File**: `backend/agents/scenario_agent.py`
**Prompt**: `backend/prompts/agents/scenario_generator/`

Generates test scenarios from a requirement. Uses LLM to produce scenarios with:
- Scenario name / title
- Description
- Priority
- Confidence score

**Configurable**: Number of scenarios via `scenario_count` parameter (default: 3)

**State writes**: `state.generated_scenarios` (appended)

**Operation**: `GENERATE_SCENARIOS`

---

### 7. HumanApprovalAgent

**File**: `backend/agents/human_approval_agent.py`
**Prompt**: (none — system-prompt driven)

A gate agent used twice in the pipeline:

| Gate | Input | Output |
|---|---|---|
| **Gate 1** (after scenarios) | `generated_scenarios` | Approved scenarios → TestCaseAgent. Rejected → END |
| **Gate 2** (after evaluation) | `generated_test_cases` | Approved TCs → PlaywrightAgent. Rejected → END |

In `human_review_enabled=false` mode, auto-approves everything above threshold. Otherwise, records pending approvals for human decision via API.

**State reads**: `state.pending_approval_ids`
**State writes**: `state.selected_scenario_ids`, `state.human_approved_test_case_ids`

---

### 8. TestCaseAgent

**File**: `backend/agents/test_case_agent.py`
**Prompt**: `backend/prompts/agents/test_case_designer/`

Generates detailed test cases from approved scenarios. Each test case includes:
- Title
- Preconditions
- Step-by-step instructions
- Expected result
- Priority
- Confidence score
- Execution type (Automated / Manual)
- Automation framework classification

**State writes**: `state.generated_test_cases`

**Operation**: `GENERATE_TESTCASES`

---

### 9. EvaluationAgent

**File**: `backend/agents/evaluation_agent.py`
**Prompt**: (self-contained logic)

Evaluates generated test cases for quality. Performs:

| Check | Method | Action |
|---|---|---|
| **Duplicate detection** | Jaccard word overlap on title+expected_result | Marks as `DUPLICATE` if above threshold (default 0.75) |
| **Relevance scoring** | LLM-based relevance to parent scenario | Marks as `NEEDS_REVIEW` if below threshold (default 0.3) |
| **Quality ranking** | Confidence score from TestCaseAgent + relevance | Sorts approved cases by confidence |

**State writes**: Sets `tc.evaluation_status` and `tc.evaluation_reason` for each test case

**Operation**: `GENERATE_TESTCASES` (sub-step, always runs after TestCaseAgent)

---

### 10. PlaywrightAgent

**File**: `backend/agents/playwright_agent.py`
**Prompt**: `backend/prompts/agents/functional_script_generator/` and `backend/prompts/agents/api_script_generator/`

Generates executable Playwright TypeScript scripts from approved test cases. Uses the QA Automation Orchestrator to classify each test case as:

- **UI Automated** → Generates full Playwright script with locators, assertions, navigation
- **API Automated** → Generates API test script with HTTP calls, payloads, response validation
- **Manual Testing** → Returns human-readable step instructions instead of a script

The prompt includes a feature context built from the requirement (title, description, acceptance criteria, business rules) and the test case's scenario. Live page evidence binds locators only on the pages it lists. On other pages, usually those behind the login, the agent builds accessible locators from the step wording and never writes a test that fails on purpose. Each test starts logged out, so a case that needs an authenticated user logs in first with `TARGET_USERNAME` and `TARGET_PASSWORD`.

Before saving a data-driven script, `WorkflowService` regenerates it once when it finds a TODO or method name used as a selector (for example `page.locator('getByLabel')`), a `page.click(selector)` style action that skips step screenshots, a forced failure or skip, `waitForTimeout`, or no `expect` assertion. The 20 datasets and their one-test-per-dataset layout are unchanged.

A data-driven script holds its datasets once as an array and runs one template test in a `for (const row of datasets)` loop, each iteration inside `test.describe(row.id)`. Template markers (`__TEST_DATA_NN__`, `__TEST_DATA_ID__`, `__TEST_TYPE__`) become `row.data["field"]`, `row.id` and `row.type`. For projects marked as a safe test environment, `PlaywrightAgent` gets a `template_recorder` (`generate_data_driven_template` in `backend/agents/script_generator_agent.py`). It records each template on the live app with one sample dataset, then writes the template from the verified trace. If recording fails or omits a marker, the agent falls back to the single LLM call.

**State writes**: `tc.playwright_script`, `tc.execution_type`, `tc.automation_framework`

**Operation**: `GENERATE_PLAYWRIGHT`

#### Live-verified script generation (Generate Script)

**Files**: `backend/agents/script_generator_agent.py`, `backend/agents/dom_distiller.py`

When **Generate Script** (`POST /api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script`) is called for a Playwright project that has a `target_url`, and the test case does not yet hold 20 distinct TestDataAgent datasets, `WorkflowService` calls `generate_accurate_test_script(target_url, steps, test_title)` instead of the data-driven path. A case that already holds its 20 datasets (from **Generate Test Data**) always takes the data-driven path, so its script keeps one test per dataset. That function:

1. Opens a headless Chromium browser in Python Playwright (set `HEADLESS_AGENT=false` to watch it) and navigates to the project's target URL.
2. For each test case step, runs up to 6 micro-actions. Each one reads the live ARIA snapshot and visible interactive elements (with a recommended selector each, never raw HTML), asks the LLM for an `AgentDecision` (`click`, `fill`, `select_option`, `press`, `assert_visible`, `assert_text` or `done`), and runs it on the page with a 5000 ms timeout. Actions that work go into the verified trace. When an action fails, its error is sent back to the LLM so it picks another locator.
3. Closes the browser, then asks the LLM to write a TypeScript `@playwright/test` file that uses only the selectors from the verified trace.

The script is saved to `tc.playwright_script` with `script_validation_status = unverified`. Projects without a `target_url` keep the data-driven path described above. LLM calls go through `LLMService`, and tests inject a fake generator through `WorkflowService(script_generator=...)`.

---

### 11. ExecutionAgent

**File**: `backend/agents/execution_agent.py`
**Prompt**: `backend/prompts/agents/qa_test_executor/`

Executes Playwright TypeScript tests against the target application. Features:
- Launches Playwright via `npx playwright test`
- Captures screenshots, video, and trace files
- Streams real-time logs via callback (`on_log`)
- Determines pass/fail based on exit code and assertion results

**State writes**: `state.execution_results` (appends `ExecutionResult`)

**Operation**: `EXECUTE`

---

### 12. ExecutionAnalysisAgent

**File**: `backend/agents/execution_analysis_agent.py`
**Prompt**: `backend/prompts/agents/execution_analysis/`

Analyzes failed test executions to determine:
- **Root cause** — Summary of why the test failed
- **Failure category** — Functional, UI, Data, Environment, Flaky, Timeout
- **Flakiness detection** — Identifies tests that pass intermittently
- **JIRA bug candidate** — Flags failures suitable for automated bug creation

**State writes**: Sets `result.failure_category`, `result.root_cause_summary`, `result.jira_bug_id`

**Operation**: `EXECUTE` (sub-step)

---

### 13. DefectManagementAgent

**File**: `backend/agents/defect_management_agent.py`
**Prompt**: `backend/prompts/agents/defect_agent/`

Manages the defect lifecycle:
- Checks for duplicate bugs in JIRA
- Creates new JIRA bug tickets for confirmed failures
- Updates existing bugs with retest results
- Syncs defect status back to the platform

**State writes**: Sets `result.jira_bug_id`, `result.jira_bug_url`, `result.retest_pending_candidate`

**Operation**: `EXECUTE` (sub-step)

---

### 14. ReportAgent

**File**: `backend/agents/report_agent.py`
**Prompt**: `backend/prompts/agents/qa_reporting/`

Generates execution reports in multiple formats:
- **JUnit XML** — Standard CI/CD integration format
- **JSON report** — Structured execution summary
- **Summary metrics** — Pass/fail counts, duration, coverage

**State writes**: `state.execution_report`

**Operation**: `EXECUTE` (final sub-step)

---

### 15. JiraSyncAgent

**File**: `backend/agents/jira_sync_agent.py`

Bidirectional JIRA synchronization:
- **SYNC_USER_STORY**: Creates/updates JIRA Stories from scenarios
- **SYNC_BUG**: Creates JIRA Bugs from execution failures
- Tracks sync status and last-synced timestamps on entities

**State writes**: Updates `jira_issue_key`, `jira_sync_status`, `jira_last_synced_at` on scenarios and test cases

**Operations**: `SYNC_USER_STORY`, `SYNC_BUG`

---

## Automation Orchestrator

**File**: `backend/agents/automation_orchestrator_agent.py`
**Prompt**: `backend/prompts/agents/automation_orchestrator/`

A meta-agent that classifies test cases for the correct automation pathway. Runs before PlaywrightAgent to determine:

| Classification | Action |
|---|---|
| `UI Automated` | Route to PlaywrightAgent for browser script |
| `API Automated` | Route to API script generator |
| `Manual Testing` | Skip script generation |

Also supports framework selection: Playwright, Selenium, or Cucumber.

---

## Agent Communication Model

```
                    ┌─────────────────────────┐
                    │     WorkflowState        │
                    │                         │
                    │  requirement             │ ◄── RequirementAnalystAgent
                    │  generated_scenarios     │ ◄── ScenarioAgent
                    │  selected_scenario_ids   │ ◄── HumanApprovalAgent (Gate 1)
                    │  generated_test_cases    │ ◄── TestCaseAgent
                    │  └─ evaluation_status    │ ◄── EvaluationAgent
                    │  human_approved_tc_ids   │ ◄── HumanApprovalAgent (Gate 2)
                    │  └─ playwright_script    │ ◄── PlaywrightAgent
                    │  execution_results       │ ◄── ExecutionAgent
                    │  └─ failure_category     │ ◄── ExecutionAnalysisAgent
                    │  └─ jira_bug_id          │ ◄── DefectManagementAgent
                    │  execution_report        │ ◄── ReportAgent
                    │  logs                    │ ◄── (all agents append)
                    └─────────────────────────┘
```

Each agent reads only what it needs and writes only its slice. No agent modifies another agent's output — this keeps the pipeline deterministic and auditable.
