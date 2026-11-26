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

Optionally maps requirements to existing feature inventory if RAG is enabled. Helps avoid duplicate scenario generation. It searches the project's own knowledge base through `KnowledgeService.search` (top 3, using `state.project_id`), and skips without failing when the state has no project, the project has no dataset, or the search fails (spec 0006, AC-23).

**State writes**: `state.requirement.feature_mapping`

**Operation**: `INGEST` (optional second stage)

---

### 3a. KnowledgeRetrievalAgent

**File**: `backend/agents/knowledge_retrieval_agent.py`
**Prompts**: `backend/prompts/agents/knowledge_retrieval/` (`grade.md`, `rewrite.md`)

The graded, self correcting retrieval stage (spec 0006). Four graph nodes, one per stage, run only when `RAG_WORKFLOW_RETRIEVAL_ENABLED` is true:

| Node | Feeds | Source types searched | Query |
|---|---|---|---|
| `retrieval_requirements` | RequirementAnalystAgent | `document`, `jira_story` | requirement title, description, acceptance criteria |
| `retrieval_scenarios` | ScenarioAgent | `document`, `jira_story` | same |
| `retrieval_test_cases` | TestCaseAgent | `document`, `jira_story`, `test_case`, `locator` | same |
| `retrieval_analysis` | ExecutionAnalysisAgent (BugReportingAgent reuses it) | `execution_failure`, `defect`, `test_case`, `locator` | up to 5 failed runs (test case title and error), plus the requirement title when present |

Each attempt searches with `RAG_TOP_K`, grades every new chunk in one batched call on the `retrieval` LLM role (`{"relevant": [ids]}`), and keeps the relevant ones. With fewer than `RAG_MIN_RELEVANT_CHUNKS` and attempts left (`RAG_MAX_RETRIEVAL_ATTEMPTS`), it asks the LLM to rewrite the query and searches again. Chunks are deduplicated by chunk ID, then sorted by score and capped at `RAG_TOP_K`. Unparseable grades keep the top `RAG_MIN_RELEVANT_CHUNKS` by score, marked `graded: false`.

It never fails the workflow: RAG disabled, no `project_id`, no dataset, an empty query, a search error, or a busy retrieval role all end with `retrieval_status: skipped` and empty context.

**State writes** (owned only by this agent): `retrieval_status` (`ok`, `empty`, `skipped`), `retrieval_query`, `retrieval_attempts`, `retrieval_sources`, `retrieved_context`

**Tracing** (AC-24): with LangSmith tracing on, every search, grade, and rewrite records a span (`knowledge_retrieval.search`, `.grade`, `.rewrite`) with metadata `stage`, `attempt`, `query`, `source_types`, `returned_source_refs`, and `relevant_count`. On a search span `relevant_count` is the running total before that search; on a grade span it is how many chunks were kept; on a rewrite span it is the total that triggered the rewrite. With tracing off or langsmith not installed, nothing is sent and nothing fails.

**Measuring it** (AC-25): `RetrievalEvalService` (`backend/services/retrieval_eval_service.py`) returns recall@k and MRR for labelled cases; `python -m backend.scripts.eval_retrieval` runs it live.

**Consumers**: ScenarioAgent, TestCaseAgent, RequirementAnalystAgent, ExecutionAnalysisAgent, and BugReportingAgent append a delimited "Project knowledge" block (`backend/agents/knowledge_context.py`) to their prompt when `retrieved_context` is not empty. With no context their prompts are unchanged.

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

| **Groundedness** (spec 0006, AC-26) | One batched LLM judge call on the `analysis` role, given the test cases and `state.retrieved_context` (prompt `backend/prompts/agents/evaluation/groundedness.md`) | Sets `tc.groundedness_score` (0 to 1) and `tc.unsupported_claims`. Runs only when `retrieved_context` is not empty; a failed call, a busy pool, or unusable output leaves them `None` and `[]` with a warning. Never changes `evaluation_status` |

**State writes**: Sets `tc.evaluation_status`, `tc.evaluation_reason`, `tc.groundedness_score`, and `tc.unsupported_claims` for each test case

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

Before saving a data-driven script, `WorkflowService` regenerates it once when it finds a TODO or method name used as a selector (for example `page.locator('getByLabel')`), a `page.click(selector)` style action that skips step screenshots, a forced failure or skip, `waitForTimeout`, or no `expect` assertion. The scripted datasets and their one-test-per-dataset layout are unchanged.

A data-driven script covers only the first 5 of the test case's datasets (`_MAX_SCRIPT_DATASETS` in `backend/agents/playwright_agent.py`). The other datasets stay stored on the test case, but they get no generated test and are left out of the field notes the model sees. The script holds its datasets once as an array and runs one template test in a `for (const row of datasets)` loop, each iteration inside `test.describe(row.id)`. Template markers (`__TEST_DATA_NN__`, `__TEST_DATA_ID__`, `__TEST_TYPE__`) become `row.data["field"]`, `row.id` and `row.type`. Inside a template literal's `${...}` expression a quoted marker is bound as code, so `` `#select${'__TEST_DATA_05__'.toLowerCase()}` `` reads the row value instead of becoming literal `${...}` text; the fallback prompt also asks the model to assign a marker to a const before transforming it. For projects marked as a safe test environment, `PlaywrightAgent` gets a `template_recorder` (`generate_data_driven_template` in `backend/agents/script_generator_agent.py`). It records each template on the live app with one sample dataset, then writes the template from the verified trace. If recording fails or omits a marker, the agent falls back to the single LLM call. When that LLM template leaves out a required marker, the agent retries up to twice (`_TEMPLATE_MARKER_RETRIES`). Each retry hands back the rejected script with a note on how to use each missing marker, and the agent fails only when a marker is still missing after the last retry. Both the recorder and the fallback prompt get one note per marker, judged from all datasets: a field joined with `; ` is a list the script loops over (`'__TEST_DATA_NN__'.split('; ')`, one action per item), a product or item field (a list or a single item) is acted on by finding the item's card or row by its visible name and then the button inside it, never by putting the item name into the button's accessible name, an `expected_` field is asserted rather than typed, a field holding only true/false or yes/no values is applied to a checkbox or option, and vault references are login credentials. A field that names a page control rather than holding data (a radio button or element id such as `radio_0`, a selector, or a field named `..._selector`, `..._locator` or `..._xpath`; `names_page_control` in `backend/agents/test_data_agent.py`) gets no marker: requiring one forced the guessed name into a selector such as `input[name="radio_0"]`, which timed out. The step text says which control to use, so the recorder finds it on the page, and the field stays in the dataset rows unused. `TestDataAgent` also drops such fields from new datasets and asks the model not to create them. While recording, the recorder reads the options (labels and values) of every dropdown it picks from and hands them back through `select_options`. The agent links each dropdown to its field through the template's `selectOption('__TEST_DATA_NN__')` line, then replaces each dataset's value with the real option it means: same text ignoring case, spaces and punctuation (`MasterCard` becomes `Master Card`), or a near spelling (`Hotel Cornice` becomes `Hotel Corniche`). Test data is guessed without seeing the app, and `selectOption` matches exactly, so a near miss used to wait 30 seconds and fail. A value with no close option, such as an expiry year the list does not offer, is kept and logged with the real options. A retry after a missing marker repeats the notes for the markers that were left out. `PlaywrightRunner` reads every test in the report, including tests nested in `test.describe`: the run passes only when every test passes, and its error message lists each failing dataset's title and error.

**State writes**: `tc.playwright_script`, `tc.execution_type`, `tc.automation_framework`

**Operation**: `GENERATE_PLAYWRIGHT`

#### Live-verified script generation (Generate Script)

**Files**: `backend/agents/script_generator_agent.py`, `backend/agents/dom_distiller.py`

When **Generate Script** (`POST /api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script`) is called for a Playwright project that has a `target_url`, and the test case does not yet hold 20 distinct TestDataAgent datasets, `WorkflowService` calls `generate_accurate_test_script(target_url, steps, test_title)` instead of the data-driven path. A case that already holds its 20 datasets (from **Generate Test Data**) always takes the data-driven path, so its script keeps one test per dataset. That function:

1. Opens a headless Chromium browser in Python Playwright (set `HEADLESS_AGENT=false` to watch it) and navigates to the project's target URL.
2. For each test case step, runs up to 6 micro-actions. Each one reads the live ARIA snapshot and visible interactive elements (with a recommended selector each, never raw HTML), asks the LLM for an `AgentDecision` (`click`, `fill`, `select_option`, `press`, `assert_visible`, `assert_text` or `done`), and runs it on the page with a 5000 ms timeout. Actions that work go into the verified trace. When an action fails, its error is sent back to the LLM so it picks another locator. A verification step (one that says verify, confirm, check, should, or expected, or has an expected result) must record an `assert_visible` or `assert_text` on the current page. Its first `done` without one is sent back once, and a second `done` is accepted so the step cannot loop.
3. Closes the browser, then asks the LLM to write a TypeScript `@playwright/test` file that uses only the selectors from the verified trace. Assertions come from the trace's assertion entries or an entry's `url`. A control filled or clicked on an earlier page, such as a login field, is never asserted after the page changes, because that check fails whenever the step succeeds. The data-driven template recorder follows the same rules.

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

The log analyser. It triages **every** non-passed result in `state.execution_results`. The analysis lives in `backend/services/log_analysis_service.py` (`LogAnalysisService`), which the batch run (`ExecutionService.start_batch_run`) and the webhook path (`ExecutionService.finalize_execution`) also call before saving. So every failed run is triaged, whichever way it ran.

**Evidence** (`backend/services/log_evidence_collector.py`, text only, no images). It is read from the run folder under `backend/playwrightt/artifacts/`:
- `report.json`: error message, stack, snippet and location, all errors, and the test's stdout and stderr
- `trace.zip`: test steps up to the failing step, console errors and warnings, and failed network requests (method, URL without query string, status; never headers or bodies)
- `error-context.md`: the page snapshot (aria tree) at failure
- `result.timeline`: entries of type `error`

Each section is size-capped. Vault credentials and the test case's test data values are redacted. Artifacts are overwritten when the same test case runs again, so evidence is read right after the run.

**Run history** (`backend/services/run_history.py`). It reads the last 10 earlier runs of the same test case and computes:
- pass/fail counts and outcome flips
- how often the same normalized error signature repeats
- a deterministic `flaky_score` from 0 to 1

**Decides**:
- **Failure category**: exactly one of `Product Bug`, `Test Environment Issue`, `Flaky Test / Automation Issue`. An unrecognized value falls back to `Test Environment Issue`.
- **Flaky override**: a `Product Bug` with confidence below 0.7 on a history with `flaky_score` ≥ 0.5 is reclassified as `Flaky Test / Automation Issue`.
- **Retry and bug candidate**: `suggest_retry`, and `retest_pending_candidate` (only for a Product Bug).
- **LLM failure**: the category falls back to `Test Environment Issue` with `suggest_retry=true`. Evidence and the flaky score are still saved.

**State writes** (per non-passed result):
- `failure_category`, `root_cause_summary`, `trace_analysis`, `screenshot_findings`
- `suggest_retry`, `retest_pending_candidate`
- `failing_step`, `evidence` (quoted log lines), `confidence`, `flaky_score`
- `frontend_error_detected`, `frontend_error_details`, `run_history_summary`

**Operation**: `EXECUTE` (sub-step)

---

### 13. BugReportingAgent

**File**: `backend/agents/bug_reporting_agent.py`
**Service**: `backend/services/bug_report_service.py` (`BugReportService`)
**Prompts**: `backend/prompts/agents/bug_reporting/roles_and_responsibilities.md` (draft), `duplicate_search.md` (Jira search terms)

Replaces DefectManagementAgent. For every failed result triaged as **Product Bug**, it writes a structured bug report **draft**. A person reviews the draft in the Bugs view and submits it to Jira. Nothing reaches Jira without that review unless the project turns on auto-submit.

How a draft is built:
- **Content.** Title, summary, steps to reproduce, expected and actual result, severity and priority come from the `analysis` LLM role. The prompt gets the test case and every triage field: failing step, root cause, evidence, confidence, frontend errors and run history.
- **Fallback.** If the LLM fails, the draft is built deterministically from the test case and the error.
- **Redaction.** Vault credentials and test data values are replaced with `[redacted]`.
- **Dedupe.** One open report covers one error signature (`run_history.error_signature`) per test case. A repeat failure is added to `occurrences`; if that report is already in Jira, the Jira issue also gets a comment.

What happens on submit (`POST .../bug-reports/{id}/submit`):
1. **Duplicate search** in the project's Jira key, among open bugs. It looks first for the label `bugsig_<signature>`, then for LLM-chosen search terms. The JQL is built by the service, never by the LLM.
2. **If a duplicate is found**, the existing issue gets a comment and the report becomes `linked_duplicate`.
3. **Otherwise a Bug is created.** It is labelled `platform_sync`, `bug`, `test_<id>`, `scenario_<id>` and `bugsig_<signature>`. The screenshot and trace are attached, and the bug is linked to the scenario's Story.
4. **Run and test case updated.** The execution gets `jira_bug_id` and `jira_bug_url`; the test case gets `jira_issue_key`, `jira_issue_url` and `jira_sync_status`.
5. **Jira errors** set `submit_failed` with `submit_error`. The report stays editable and can be submitted again.

Report statuses: `draft`, `submit_failed`, `submitted`, `linked_duplicate`, `resolved` (set by the Jira webhook) and `dismissed`.

**Auto-submit**: when the project sets `bug_auto_submit` and the triage confidence is at least `bug_auto_submit_min_confidence` (default 0.85), the draft is submitted at once.

**Other entry points**: `ExecutionService._triage` drafts for batch and webhook runs. `POST .../executions/{id}/bug-report` drafts on request for any failed run. The `sync_bug` operation (JiraSyncAgent) drafts, then submits.

**State writes**: `state.bug_report_ids`, `result.bug_report_id`. It writes `result.jira_bug_id` and `result.jira_bug_url` only when auto-submit runs or the report was already filed.

**Operation**: `EXECUTE` (sub-step, between ExecutionAnalysisAgent and ReportAgent)

It reads only the latest result for each test case, so a test case that passed on a heal rerun gets no bug report (spec 0011).

---

### SelfHealingAgent (spec 0011)

**File**: `backend/agents/self_healing_agent.py`
**Services**: `backend/services/object_repository_healing.py` (`LocatorHealingService`, stage 1) and `backend/services/script_repair_service.py` (`ScriptRepairService`, stage 2)
**Prompt**: `backend/prompts/agents/self_healing/roles_and_responsibilities.md` (LLM fallback only). Stage 2 uses PlaywrightAgent's own prompt with `repair_feedback`.

Runs between ExecutionAgent's analysis and BugReportingAgent, only after a failure. Stage 1 runs when every stored locator of an object repository element failed. Stage 2 runs when the test still fails with every locator resolved and the log analyser's RCA is `Flaky Test / Automation Issue` at confidence 0.6 or more. A passed run is never processed.

- **Heal phase.** For each failing key, it first applies the failed run's markers, then reads candidates only from that run's `harvest.json` (pages matching the element's `url_pattern`), redacted like a harvest ingest. It scores them with fixed rules (test id, role, id or name, label or name similarity, placeholder, element type). A score of at least 0.85 with a 0.15 lead is applied; an unclear score from 0.5 asks the `analysis` LLM to pick one of the top 5 candidates by index (page text is marked untrusted, and the final confidence is capped at the candidate's score plus 0.2). A confident match is applied: the candidate's locators go first, the old ones stay as backups, the fingerprint moves, and `locators_version` goes up. Anything less certain is saved as a suggestion. An element a tester edited is never applied automatically. When a heal is applied, the failed run's evidence is copied to `heal-<attempt>/` and the test case is rerun once.
- **Verify phase.** After the rerun, a heal stays applied when every rerun passed. If the healed key resolved but the test still failed for an automation reason, the heal waits for stage 2. Otherwise the old locators and fingerprint go back and the heal becomes a suggestion.
- **Script repair phase (stage 2).** It regenerates the script with PlaywrightAgent:
  - **Inputs.** The repair feedback is built from the RCA (`root_cause_summary`, `failing_step`, `trace_analysis`, up to 8 evidence lines). Run text is wrapped as untrusted data, redacted, and capped at 2000 characters. The page evidence is the run's harvest. The prompt also lists the object repository, including any heal from stage 1.
  - **Gates.** The new script is applied only when it passes the validator, uses only known keys, keeps at least as many `expect(` assertions, and leaves the steps, expected result, and test data unchanged. A script that fails a gate becomes a suggestion with no rerun.
  - **Saving.** An applied fix is saved to the test case (keys only, never rendered) and to the state's copy, then rerun once.
- **Script verify phase.** A fix whose rerun passed stays, along with any heal that waited for it. A fix whose rerun failed is restored and becomes a suggestion, and the waiting heal is put back too.

**State writes**: `heal_results`, `heal_attempts`, `heal_rerun_test_case_ids`, `script_heal_results`, `script_repair_attempts`, `markers_applied_result_ids`, the failed result's artifact paths, and the state's copy of a repaired script. It never raises and never changes a result's status.

**Operation**: `EXECUTE` (sub-step, only when routed)

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
                    │  └─ jira_bug_id          │ ◄── BugReportService (on submit)
                    │  execution_report        │ ◄── ReportAgent
                    │  logs                    │ ◄── (all agents append)
                    └─────────────────────────┘
```

Each agent reads only what it needs and writes only its slice. No agent modifies another agent's output — this keeps the pipeline deterministic and auditable.
