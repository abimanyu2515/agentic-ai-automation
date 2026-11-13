# Services

## Overview

Services contain backend business behavior that supports API routes and workflow agents. They coordinate persistence, execution, integrations, credentials, and reporting.

## Key files

| File | Owns |
|---|---|
| `backend/services/workflow_service.py` | Workflow operations and project and requirement ownership checks |
| `backend/services/playwright_runner.py` | Playwright subprocess execution |
| `backend/services/batch_queue_manager.py` | Batch execution queue behavior |
| `backend/services/recovery_manager.py` | Execution recovery behavior |
| `backend/services/vault_service.py` | Encrypted project credential storage |
| `backend/services/grounded_playwright_service.py` | Inspects the target app, generates scripts from that evidence, and validates them |
| `backend/services/playwright_script_validation.py` | Checks generated script locators against inspected page evidence |
| `backend/services/locator_resolver.py` | Fallback locator strategy, used only when the primary locator fails |
| `backend/services/llm.py` | The only litellm importer; reports every completion attempt for token usage |
| `backend/services/token_usage_service.py` | Token usage recording (background writer), pricing, aggregation, budget and UI settings |
| `backend/services/llm_context.py` | Attribution context (agent, operation, project, run) for token usage records |
| `backend/services/navigation_planner.py` | Target agnostic navigation over generic states from `backend/config/app_states.json` |

## Conventions

* Keep API routes thin and put business behavior in services.
* Use the repository abstraction for project and workflow persistence.
* Inject LLM and runner dependencies so service tests can use local test doubles.
* Resolve credentials through `VaultService` and pass them to runners through environment variables.
* Capture the selected project configuration before queueing execution work.
* Construct `LLMService(role=..., agent="<agent_name>")` so token usage is attributed. New `WorkflowService` operations get `@llm_operation("<name>")`.
* Wrap callables handed to a `ThreadPoolExecutor` with `run_in_context(fn)`; otherwise LLM calls in worker threads lose their project and run attribution.

## Gotchas

* `playwright_runner.py` is the only direct Playwright subprocess integration.
* Never persist passwords or use a global application URL in place of the selected project's target URL.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
