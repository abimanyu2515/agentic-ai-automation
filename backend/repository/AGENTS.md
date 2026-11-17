# Repository

## Overview

This area provides the common persistence interface and its JSON and PostgreSQL implementations. Application services should use the repository abstraction instead of constructing repositories inside workflow logic.

## Key files

| File | Owns |
|---|---|
| `backend/repository/project_repository.py` | Repository interface and JSON implementation |
| `backend/repository/postgres_project_repository.py` | SQLAlchemy persistence implementation |
| `backend/repository/chat_repository.py` | Chat thread and message interface plus JSON store (`backend/database/chat_store.json`) |
| `backend/repository/postgres_chat_repository.py` | SQL implementation of the chat repository |
| `backend/repository/token_usage_store.py` / `postgres_token_usage_store.py` | Token usage records, JSON and SQL |
| `backend/repository/object_repository_store.py` / `postgres_object_repository_store.py` | Object repository locators, JSON and SQL |

## Conventions

* Keep JSON and PostgreSQL behavior aligned for projects, test data, and execution results.
* Use explicit transaction methods when several repository operations must be atomic.
* After a PostgreSQL query or write fails, roll back and remove the scoped session before continuing.
* Do not pass the raw SQLAlchemy session across service or agent boundaries without an explicit transaction contract.
* Persist project configuration snapshots with execution results so later project edits do not change historical execution context.
* Chat, token usage, and object repository stores follow the same provider setting as `get_project_repository()`.
* A chat action's status changes only through `compare_and_set_action_status`.
* JSON stores write through a temp file plus an atomic rename, under a process lock.

## Gotchas

* The PostgreSQL implementation uses a scoped session. Reusing it after an unhandled database error can poison all later operations.
* Canonical project URLs are normalized before persistence and are intended to be unique.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching it, later runs treat it as curated and will flag rather than overwrite it._
