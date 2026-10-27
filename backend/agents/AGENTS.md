# Agents

## Overview

You are in the workflow agent area when you change how requirements become scenarios, test cases, scripts, results, or reports. This area owns the 18 agents in `backend/agents/` and their prompts in `backend/prompts/agents/`. You may start with `backend/graph/workflow.py` to see the order, then open the one agent you need.

## Key files

| File | Owns |
|---|---|
| `backend/agents/base.py` | Base contract for every agent |
| `backend/agents/supervisor_agent.py` | Routing choice for the next node |
| `backend/agents/requirement_analyst_agent.py` | Requirement enrichment from raw text |
| `backend/agents/scenario_agent.py` | Scenario creation from requirements |
| `backend/agents/test_case_agent.py` | Test case detail from approved scenarios |
| `backend/agents/evaluation_agent.py` | Duplicate and relevance checks for test cases |
| `backend/agents/human_approval_agent.py` | Approval gates for scenarios and test cases |
| `backend/agents/playwright_agent.py` | Script creation for UI and API paths |
| `backend/agents/execution_agent.py` | Browser run and result capture |
| `backend/agents/execution_analysis_agent.py` | Failure cause and flake insight |
| `backend/agents/defect_management_agent.py` | Jira bug lifecycle for failures |
| `backend/agents/report_agent.py` | Execution reports in JSON and JUnit form |
| `backend/agents/test_data_agent.py` | Row by row test data for each test case |
| `backend/agents/application_reconnaissance_agent.py` | Live, sanitized DOM inspection of the project target URL for grounded scripts |
| `backend/prompts/agents/` | Prompt text for each agent, one folder per agent |

## Conventions

* You may read only the fields your agent needs from `WorkflowState` and update only your owned fields, then return the state
* You may validate preconditions first and raise `ValueError` when input is missing, so failures stay clear and early
* You may log start and completion to `state.logs`, so runs stay easy to trace
* You may keep prompt text in `backend/prompts/agents/` separate from logic, so wording changes stay small and safe
* You may route through `SupervisorAgent` and `backend/graph/workflow.py` instead of calling the next agent directly, so order stays in one place
* You may keep project URL, framework, and credentials as project data from the repository and vault, never as fixed values in prompts

## Gotchas

* You may find `HumanApprovalAgent` used twice, once after scenarios and once after evaluation, with auto approval when human review is off
* You may find `TestDataAgent` and `ApplicationReconnaissanceAgent` are not graph nodes. `WorkflowService` runs `TestDataAgent`, and `GroundedPlaywrightScriptService` runs reconnaissance before `PlaywrightAgent`
* You may find `EvaluationAgent` marks duplicates by word overlap and low relevance items for review, then sorts the rest by confidence
* You may find `PlaywrightAgent` branches into UI scripts, API scripts, or human readable steps through the automation orchestrator choice
* You may find `docs/agents.md` holds the full per agent reference, while this file stays the short local guide you can trust first

## Related specs

* [0001 project specific testing](../docs/specs/0001-project-specific-testing/index.md)
* [0002 project creation target URL](../docs/specs/0002-project-creation-target-url/index.md)

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
