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

## Conventions

* Keep API routes thin and put business behavior in services.
* Use the repository abstraction for project and workflow persistence.
* Inject LLM and runner dependencies so service tests can use local test doubles.
* Resolve credentials through `VaultService` and pass them to runners through environment variables.
* Capture the selected project configuration before queueing execution work.

## Gotchas

* `playwright_runner.py` is the only direct Playwright subprocess integration.
* Never persist passwords or use a global application URL in place of the selected project's target URL.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
