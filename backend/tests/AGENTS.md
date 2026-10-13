# Backend tests

## Overview

This area contains pytest coverage for API routes, workflow agents, repositories, and execution behavior. Tests should remain independent from live language model providers and browser processes.

## Key files

| File | Owns |
|---|---|
| `backend/tests/conftest.py` | Shared LLM and Playwright test doubles |
| `backend/tests/test_api.py` | API and project configuration behavior |
| `backend/tests/test_project_configuration_snapshot.py` | JSON execution snapshot persistence |

## Conventions

* Use `StubLLMService` for structured agent responses and generated Playwright code.
* Use `FakePlaywrightRunner` for execution tests that do not need a real browser.
* New project API payloads must include a framework and target URL.
* Include explicit project context in agent and execution fixtures.
* Test JSON and PostgreSQL behavior separately when persistence or transaction handling is changed.
* Keep credentials out of snapshots, logs, fixtures, and generated artifacts.

## Gotchas

* A successful unit test does not prove queue recovery or live PostgreSQL behavior.
* The shared scenario fixture requires `path_type` because the scenario schema validates it.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching it, later runs treat it as curated and will flag rather than overwrite it._
