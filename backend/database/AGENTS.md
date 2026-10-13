# Database

## Overview

This area owns SQLAlchemy engine setup, ORM models, sessions, and Alembic migrations. The application can use the JSON repository while database dependent features use the SQLAlchemy database.

## Key files

| File | Owns |
|---|---|
| `backend/database/db.py` | Engine creation and session lifecycle |
| `backend/database/db_models.py` | SQLAlchemy table mappings |
| `backend/database/alembic/` | Schema migration history |

## Conventions

* The JSON provider uses a local SQLite fallback for database dependencies.
* PostgreSQL uses the configured pool settings and connection health checks.
* Use `get_db_session()` when a short lived direct database session is needed.
* Roll back immediately after every database exception, then close or remove the session before reuse.
* Add schema changes through a new Alembic migration. Project URL normalization belongs in the shared project configuration service.

## Gotchas

* A PostgreSQL transaction stays failed after one query or flush error until rollback. Later queries can raise `InFailedSqlTransaction`.
* The execution table stores the immutable project configuration snapshot without passwords.

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
