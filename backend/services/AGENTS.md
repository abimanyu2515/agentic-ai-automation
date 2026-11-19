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
| `backend/services/chat_service.py` | Chat assistant turn loop (spec 0005): saves messages, streams SSE events, runs read tools, handles action confirm |
| `backend/services/chat_tools.py` | Project scoped read tools the chat model may call |
| `backend/services/chat_actions.py` | Chat action catalog, proposal validation, and `ChatActionExecutor` for confirmed actions |
| `backend/services/script_generation_jobs.py` | In memory background jobs for live verified script generation; the page polls them |
| `backend/services/execution_service.py` | Batch execution runs and their live event logs |
| `backend/services/dashboard_metrics.py` | Dashboard metric computation, shared by the dashboard and chat tools |
| `backend/services/object_repository_service.py` | Object repository rules: CRUD, recon and harvest ingest (`ingest_recon`), run markers, detection score |
| `backend/services/object_repository_script.py` | Inserts (render) and removes (strip) the `orx` block in scripts, and rewrites recorded selectors to keys |
| `backend/services/object_repository_harvest.py` | Shared extractor loading, harvest parsing, redaction, and path normalization |
| `backend/services/object_repository_matcher.py` | `ObjectKeyResolver`: matches a recorded selector to a stored element by DOM node during generation |
| `backend/services/object_repository_scan.py` | In memory Scan now jobs |
| `backend/services/knowledge_service.py` | Per project knowledge base upload and search (spec 0006) |
| `backend/services/knowledge_indexer.py` | Background worker that indexes project knowledge into RAGFlow |
| `backend/services/retrieval/` | Retriever interface, RAGFlow implementation, and test doubles |

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
* The chat assistant is a service level loop, not a `WorkflowState` agent. Pass every dependency through its constructor so tests inject an LLM stub and an in memory store.
* The chat model can only propose an action through a `propose_<type>` tool. Nothing runs until the user confirms, and confirm runs the saved `params`, never values the browser sends.
* Every chat tool checks that each ID belongs to the current project and never returns credentials, vault data, passwords, or API keys.
* Script generation jobs live in memory. A backend restart loses running jobs, and polling then reports the job as not found.
* Stored scripts hold object repository keys only. Strip happens only in `project_service`'s test case script update; render happens in the `TestCase` response mapper, the workspace responses, and `ExecutionAgent`. Never save a rendered script.
* Only `ExecutionService.finalize_execution` applies `[OBJREPO]` markers, and only `ExecutionService.apply_object_repository_harvest` ingests harvests. A harvest error must never change a run's result or fail a webhook.
* The `orx` strategy mapping exists in three places that must agree: the injected block in `object_repository_script.py`, `playwright_locator` in `object_repository_matcher.py`, and the in page match table in `backend/playwrightt/lib/object-harvest.js`.
* Get the retriever with `get_retriever()`. With RAG off it is a `NullRetriever`.
* Knowledge indexer queue items carry IDs only, and every remote RAGFlow delete is saved as a `KnowledgeRemoteDelete` row before it is sent (spec 0007).

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
