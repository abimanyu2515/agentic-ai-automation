# 0007. Persist pending RAGFlow deletes and retry them until they succeed

**Date**: 2026-11-18
**Umbrella**: [0006. Agentic RAG](index.md)

## Summary

When the app deletes something from RAGFlow (a document, a knowledge entry's remote copy, or a whole project dataset), that delete now gets saved as a row in a small table before it runs. The row stays until RAGFlow confirms the delete, or says the item is already gone. Rows are retried at startup and every 30 minutes, so a RAGFlow outage or a server restart no longer leaves project data behind in RAGFlow. You can see how many are outstanding on the knowledge status route, and through the reindex script.

## Context

Spec 0006 runs every remote delete as an in memory job in `KnowledgeIndexer` (the background worker that talks to RAGFlow). `_delete_remote` and `_delete_dataset` retry a transient failure after 10, 60, and 300 seconds, then only log a warning. Nothing is stored. The startup sweep (0006 AC-4) looks only at documents. So a project deleted while RAGFlow is down keeps its dataset forever, and because the project row no longer points at it, the app can never find it again. The same happens to a document or entry whose remote delete fails, or that was queued just before a restart. The 2026-11-18 review (`docs/reviews/2026-11-18-main.md`) confirmed this as a Major finding: a data retention and storage problem.

Two smaller leaks share the cause. A document that fails parsing or times out keeps its remote copy, and a retry uploads a second one. When two first indexes race, `_ensure_dataset` deletes the extra dataset once and only logs a failure.

Forces: the JSON store is the default and has no transactions; PostgreSQL is optional. The local delete must never fail because RAGFlow is down (0006 AC-5, AC-10). Only one server process is supported (0006 AC-2), but the reindex script runs as a second process.

## Requirements

**User stories**:
- As a QA lead, I want data I delete from a project to be removed from RAGFlow even if RAGFlow was down or the server restarted, so that deleted project data does not linger on the RAGFlow server.
- As an operator, I want to see which RAGFlow deletes are still outstanding and why, so that I can fix a broken key or server and confirm the backlog clears.

**Acceptance criteria**:

*Persisting the delete*
- **AC-1**: Every remote delete the app queues is saved as a `KnowledgeRemoteDelete` row before its in memory job is scheduled. That covers each `reason`:
  - `document_deleted`: `KnowledgeService.delete`
  - `project_deleted`: `KnowledgeService.project_deleted`
  - `entry_removed`: the indexer's `_remove_entry`
  - `entry_replaced`: changed content replacing an entry's old remote document in `_index_source`
  - `upload_raced_delete`: a document deleted while it was uploading
  - `parse_failed`: AC-9
  - `duplicate_dataset`: AC-10

  The row is written after the local change has succeeded, never before, so a failed local delete can never remove a live remote copy. The persistence lives inside `enqueue_remote_delete` and `enqueue_dataset_delete`, so callers only add a `reason`.
- **AC-2**: One row covers one remote target: one remote document (`kind=document`) or one whole dataset (`kind=dataset`). Queuing a target that already has a row adds nothing and returns the existing row.
  - Queuing a `dataset` row removes every `document` row with the same `dataset_id`.
  - Queuing a `document` row while a `dataset` row exists for its `dataset_id` adds nothing.
  - The worker drops a `document` row whose dataset has a `dataset` row, without calling RAGFlow.
- **AC-3**: If saving the row fails (for example a PostgreSQL error), the repository rolls back and clears the session. The indexer logs at error level with the project, dataset, remote ID, and reason, and still schedules the in memory job without a row (today's behavior). The local delete and the request that triggered it still succeed.

*Running and retrying*
- **AC-4**: The worker makes one RAGFlow call per row: `retriever.delete(target, [remote_doc_id])` or `retriever.delete_dataset(target)`, where the target is the row's `project_id` and `dataset_id`. On success it removes the row and logs at info. A "not found" answer (`RemoteNotFound`, see AC-13) also counts as success and removes the row.
- **AC-5**: A transient failure (connection error, timeout, 5xx, or "still parsing") is retried on `RAG_INDEX_RETRY_DELAYS`, as today. When those run out, or the failure is not transient (for example 401 or another 4xx), the worker:
  - adds 1 to `attempts`
  - sets `last_error` and `last_attempt_at`
  - logs a warning
  - leaves the row in place

  A row is never marked failed and never given up on. Failures from the reindex script (AC-12) count toward `attempts` too. `last_error` is cut to its first 500 characters.
- **AC-6**: On startup, when RAG is active, the sweep queues every row (in addition to the 0006 AC-4 documents), reading them with `list_remote_deletes(None)`. It returns documents plus rows queued, and logs both counts. Rows survive a restart.
- **AC-7**: A timed resweep job queues every row that is not already queued in memory, then schedules itself again `RAG_DELETE_RESWEEP_MINUTES` (default 30) later.
  - **First run.** `start()` schedules it one full interval out, because `sweep()` already covers startup.
  - **Always reschedules.** It reschedules in a `finally`, so a repository error never stops it.
  - **One job per row.** The indexer tracks queued row IDs in `_queued_deletes`, so a row never has two jobs at once. An ID leaves the set in `_run`'s `finally`, whatever the outcome (success, AC-5 recording, a dropped row, or an unexpected exception), unless `_retry` scheduled the job again.
- **AC-8**: While RAG is disabled (`retriever.is_active()` false), rows are kept, nothing calls RAGFlow, and `attempts` does not change. The startup sweep and the resweep do nothing, and the resweep still schedules itself again. Once RAG is enabled, the next startup or resweep picks the rows up.

*Closing the related leaks*
- **AC-9**: Whenever the indexer marks a document `failed` while it has a `remote_doc_id`, it also clears that ID and queues a `parse_failed` delete of the old remote document. This covers every `_fail` path: a parse failure, a parse timeout, poll retries running out, and the generic exception handler in `_run`. `_fail` reads `remote_doc_id` and clears it inside `write_guard`, in the same write as the status. The delete is queued after the write. If the document was already deleted, nothing is written, and the earlier `document_deleted` row covers the remote copy. A later retry uploads one fresh remote document, not a second one. A failure inside `_start_parse` (upload succeeded, parse start failed) loses the remote ID before the indexer sees it; that case is a Follow-up.
- **AC-10**: When `_ensure_dataset` finds that another writer saved a dataset first, it queues a `duplicate_dataset` delete of the extra dataset through `enqueue_dataset_delete`, instead of deleting it in place.

*Seeing the backlog*
- **AC-11**: `GET /api/v1/projects/{project_id}/knowledge/status` adds `pending_deletes: {count, oldest_created_at, last_error}` for that project's rows, using the existing auth (bearer, project access, `Knowledge:view`).
  - `last_error` is the error of the row with the latest `last_attempt_at`. It is null when no row has been attempted yet.
  - With no rows, the value is `{count: 0, oldest_created_at: null, last_error: null}`.
  - Existing fields are unchanged.
- **AC-12**: `python -m backend.scripts.reindex_project_knowledge --pending-deletes [--run]` covers every row, including those of deleted projects. It is a third choice in the existing `--project` / `--all` group.
  - **Listing.** It always prints one line per row: project, kind, dataset, remote ID, reason, attempts, age (as `3d 4h`, `2h 5m`, or `40s`), and `last_error` cut to 120 characters. Rows print even when RAG is disabled.
  - **When it runs the rows.** On PostgreSQL it runs them by default. On the JSON store it only lists them unless you pass `--run`, because the server and the script would both rewrite the same file. The `--run` help text says to stop the server first when using the JSON store. With RAG disabled it never runs them.
  - **How it runs them.** It makes one attempt per row, through the indexer's row handler, with no short retries and no resweep job. It never calls `start()` or `drain()`. A failure is recorded as in AC-5.
  - **Output and exit code.** It prints `deleted N, still pending M`, and exits 1 when any row remains (including when it only listed), else 0.
  - **Safety.** Running alongside the server on PostgreSQL is safe, because deletes are idempotent (AC-4) and removing a row that is already gone does nothing.
- **AC-13**: The retriever reports "already gone" as `RemoteNotFound` (a subclass of `RetrieverError`, not transient). `InMemoryRetriever` raises it for unknown IDs.
  - `RetrieverError` gains `status_code` and `code` (RAGFlow's body code), both optional, and `_call` fills them on every error it raises.
  - **Documents.** When `RAGFlowRetriever.delete` gets a short `deleted` count, it first stops the parse (as today). Then it checks the document with `GET /api/v1/datasets/{id}/documents?id=`. An absent document raises `RemoteNotFound`. A present one raises the existing transient "still parsing" error.
  - **Datasets.** `delete_dataset` raises `RemoteNotFound` when a 404 comes back, or when the body code and message match RAGFlow's "dataset not found or not owned" answer. Check that answer against the installed RAGFlow version in build task 2.
  - **Unrecognised answers.** Any other answer stays a plain `RetrieverError`, so the row is kept rather than removed by mistake.

## Decision

**Chosen option**: a pending delete table (an "outbox": work is written down before it is done) behind the existing indexer queue.

Every remote delete becomes a `KnowledgeRemoteDelete` row, saved through the repository (JSON and PostgreSQL), and removed only on success or "not found". The in memory heap still runs the jobs and short retries. The table is the durable record that the startup sweep and a 30 minute resweep replay.

**Why** (short inline rationale; this child has no `rationale.md`):
- *Fields on `Project` and `Document`* (tombstones, meaning soft deleted records kept only to remember cleanup) were rejected. They change document delete semantics, and knowledge entries and duplicate datasets have no natural home in them.
- *A persistent queue for every indexer job* was rejected as much larger than the problem. Uploads and polls are already recovered by the 0006 startup sweep. Only deletes lose their target, because the local record is gone. It stays a Follow-up in 0006.
- *Writing the row before the local delete* was rejected. A failed local delete would then remove a live remote copy. A crash in the tiny gap between local delete and row save is accepted: it costs one orphan (the same as today), never data.
- *Retrying forever in memory with a capped backoff, with no resweep job*, was rejected. It is simpler (no `JOB_RESWEEP`, no `_queued_deletes`, no setting), but the heap is lost on every restart, so the table is needed anyway. Replaying the table on a timer keeps the database the single source of truth.
- *Retrying forever, with no give up state*, is safe because the work is idempotent and cheap, and it means a fixed API key or server clears the backlog with no human step. The cost is a row that can never succeed (for example RAGFlow pointed at a new server) staying visible until someone removes it. AC-11 and AC-12 make that visible.

## Feature design

**Data model sketch** (one new entity; no changes to existing ones):

| Field | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID | no | PK |
| `project_id` | UUID | no | the owning project, as a plain column with **no** foreign key. Rows must outlive their project, and `reset_demo_data` hard deletes projects in PostgreSQL |
| `kind` | str | no | `document` or `dataset` |
| `dataset_id` | str | no | the RAGFlow dataset, captured before the local delete |
| `remote_doc_id` | str | yes | set when `kind=document`, null when `kind=dataset` |
| `reason` | str | no | `document_deleted`, `project_deleted`, `entry_removed`, `entry_replaced`, `upload_raced_delete`, `parse_failed`, `duplicate_dataset` |
| `attempts` | int | no | default 0; failed attempts that reached AC-5 |
| `last_error` | Text | yes | at most 500 characters (cut before saving) |
| `created_at` | datetime (UTC) | no | |
| `last_attempt_at` | datetime (UTC) | yes | |

Constraints:
- **Uniqueness.** PostgreSQL treats nulls as distinct, so uniqueness uses two partial unique indexes: `(dataset_id, remote_doc_id) WHERE kind = 'document'` and `(dataset_id) WHERE kind = 'dataset'`.
- **Lookup index.** Add an index on `project_id` for the status count.
- **Declare them in the model.** Put all three in `KnowledgeRemoteDeleteDB.__table_args__` as well as the migration, so autogenerate and `create_all` agree (the review's index drift finding).

Layers, following the `KnowledgeEntry` pattern:
- **Domain model:** `KnowledgeRemoteDelete` in `backend/models/knowledge.py`.
- **Table:** `KnowledgeRemoteDeleteDB` (`knowledge_remote_deletes`) in `backend/database/db_models.py`, plus an Alembic revision with `down_revision = "c6f0a2d84e19"` (check that this is still the single head when you build). The test suite's `create_all` can make the table on the dev database before Alembic runs, so the migration creates the table and its indexes only when they are missing.
- **JSON store:** a top level `knowledge_remote_deletes` dict keyed by row ID. Every read, change, and write of it holds `_write_lock`.

Repository methods (interface plus both implementations):
- `add_remote_delete(row) -> KnowledgeRemoteDelete | None`: applies AC-2. It returns the existing row on a duplicate, and `None` when a dataset row absorbs it. In PostgreSQL, an `IntegrityError` from a racing insert rolls back, clears the session, and returns the row that won.
- `get_remote_delete(row_id)`
- `get_dataset_delete(dataset_id) -> KnowledgeRemoteDelete | None`: the `dataset` row for that dataset, used by the absorb check.
- `list_remote_deletes(project_id: UUID | None = None)`: `None` lists every project, including deleted ones.
- `record_remote_delete_failure(row_id, error, at)`: adds 1 to `attempts`, sets `last_error` and `last_attempt_at`; does nothing when the row is gone. PostgreSQL does this as one atomic `UPDATE ... SET attempts = attempts + 1`, and the JSON store does it under `_write_lock`.
- `remove_remote_delete(row_id) -> bool`: returns False when already gone.

Every PostgreSQL method rolls back and calls `db_session.remove()` on any database error before raising or returning, per the project rule.

**State transitions**: a row exists (outstanding) or is removed (done). There is no other state. `attempts` only grows.

**Indexer changes** (`backend/services/knowledge_indexer.py`):
- `enqueue_remote_delete(project_id, dataset_id, remote_ids, reason)` saves one row per remote ID, then schedules `JOB_REMOTE_DELETE` with `row_id`.
- `enqueue_dataset_delete(project_id, dataset_id, reason)` saves one row, then schedules `JOB_DATASET_DELETE` with `row_id`.
- `reason` is a required keyword. No test calls these two methods directly today, so only the six call sites change.
- When `add_remote_delete` returns an existing row, the indexer schedules a job only if that row's ID is not already in `_queued_deletes`. When it returns `None` (absorbed), nothing is scheduled.
- The job's `data` gains `row_id` (`None` on the AC-3 fallback) and `remote_ids` holds exactly one ID.
- A `_queued_deletes: set[UUID]` guarded by `_cond` records row IDs that have a job in the heap (AC-7 says when an ID leaves it).
- A new `JOB_RESWEEP` kind runs `_resweep()` (AC-7, AC-8). Using a timed job, not a thread, keeps it testable with the injected clock. `stop()` leaves it in the heap, and `start()` does not add a second one if one is already queued.
- `sweep()` also queues every row (AC-6), still only when RAG is active.
- **The row handler** (`_run_delete_row(job, retries=True)`, shared with the script, which passes `retries=False`):
  - **Fallback job** (`row_id is None`, the AC-3 path). It skips every row read and write, and on failure only retries and logs.
  - **Row job.** It reads the row again, and if the row is missing (another process finished it, or a dataset row absorbed it) it logs at debug and ends. For a `document` row it then calls `get_dataset_delete`, and if a dataset row exists it removes this row without calling RAGFlow. It then makes the call and applies AC-4 or AC-5.
- `_fail` applies AC-9, and `_ensure_dataset` applies AC-10.
- Every repository step is followed by `_release()`, as today.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/v1/projects/{project_id}/knowledge/status` | GET | none | existing fields plus `pending_deletes: {count: int, oldest_created_at: datetime \| null, last_error: str \| null}` | bearer, project access, `Knowledge:view` (unchanged) | 401, 403, 404 (unchanged) |
| `reindex_project_knowledge --pending-deletes [--run]` | CLI | `--run` (needed on the JSON store) | one line per row, then `deleted N, still pending M` | local shell | exit 1 when any row remains |

No new HTTP routes.

**Value sourcing**:

| Action | Value produced or displayed | Source |
|---|---|---|
| enqueue | `dataset_id` | the caller's captured value: `Project.rag_dataset_id` read before the local delete (0006 AC-5, AC-10), or the `created` ID in `_ensure_dataset` |
| enqueue | `remote_doc_id` | `Document.remote_doc_id` or `KnowledgeEntry.remote_doc_id` read before the delete, or the upload result in the race path |
| enqueue | `reason` | fixed per call site (AC-1 list) |
| enqueue | `created_at` | `datetime.now(timezone.utc)` at save |
| worker | call target | `SimpleNamespace(id=row.project_id, rag_dataset_id=row.dataset_id)` (the existing `_dataset_ref`) |
| worker | transient or not | `RetrieverError.transient` from `RAGFlowRetriever._call` |
| worker | "already gone" | `RemoteNotFound` raised by the retriever (AC-13): for documents, a short `deleted` count followed by a GET that finds no document; for datasets, a 404 or RAGFlow's matched code |
| worker | `remote_doc_id` to clear and delete on failure (AC-9) | the document read inside `write_guard` in `_fail` |
| worker | `last_error`, `last_attempt_at` | `str(exc)[:500]`; wall clock UTC (not the monotonic `self.clock`) |
| resweep | interval | `settings.rag.delete_resweep_minutes` |
| status | `count`, `oldest_created_at` | `list_remote_deletes(project.id)`: its length, and its minimum `created_at` |
| status | `last_error` | the `last_error` of that list's row with the latest `last_attempt_at`; null when none has one. `_release()` follows the read |
| script | age | now minus `created_at`, shown as `3d 4h`, `2h 5m`, or `40s` |
| script | which store | `get_project_repository()` type (JSON needs `--run`) |

**Key invariants**:
- A row is written only after the local change it cleans up has succeeded.
- A row is removed only when RAGFlow confirms the delete or says the target does not exist.
- Every remote delete goes through `enqueue_remote_delete` or `enqueue_dataset_delete`. No other code calls `retriever.delete` or `retriever.delete_dataset`, except the worker.
- A delete only ever targets the `dataset_id` saved on its row. It never reads a dataset ID from the current project, settings, or a route.
- At most one in memory job per row at a time.

**Security model**: No new routes or permissions. The status route keeps its auth, and `pending_deletes` shows only that project's rows. `last_error` holds RAGFlow's message text and never the API key (`_call` never puts the key in its errors). Stakeholders, who have `Knowledge:view`, can read it, the same as document `error` today. Remote IDs are shown only by the local script.

**Configuration required**:
- `RAG_DELETE_RESWEEP_MINUTES` (default 30, greater than 0): the resweep interval. Add it to `RAGConfig.delete_resweep_minutes`, `default_config.yaml`, `.env.example`, and the README settings table.

**Critical test scenarios** (`InMemoryRetriever` or a fake, injected clock, JSON store unless noted):
- **Restart.** Delete a document while the fake fails every call. Throw the indexer away, build a new one on the same store, make the fake succeed, and run `sweep()`. The remote document is deleted and the row is gone. Verifies **AC-1**, **AC-4**, **AC-6**.
- **Project delete during an outage.** The dataset row survives the short retries with `attempts=1` and `last_error` set. The resweep at +30 min deletes the dataset. The project's pending document rows are absorbed and never called. Verifies **AC-2**, **AC-5**, **AC-7**.
- **Not found.** The fake raises `RemoteNotFound`, and the row is removed with no retries. A mocked RAGFlow HTTP layer returns a short `deleted` count and then an empty GET, and `RAGFlowRetriever.delete` raises `RemoteNotFound`; with the document still listed, it raises the transient error. Verifies **AC-4**, **AC-13**.
- **Unexpected exception.** A repository error while reading the row ends the job, frees its ID from `_queued_deletes`, and the next resweep queues it again. Verifies **AC-7**.
- **Hard error.** The fake raises a non transient 401. There are no short retries, `attempts` is 1, and the row stays. Verifies **AC-5**.
- **Duplicates.** Queuing the same document twice gives one row and one RAGFlow call. A resweep while the job is waiting on a short retry schedules nothing new. Verifies **AC-2**, **AC-7**.
- **Order.** When the local `delete_document` raises, no row is written and RAGFlow is not called. Verifies **AC-1**.
- **Save fails.** A repository whose `add_remote_delete` raises still deletes locally, logs an error, and runs the in memory job. Verifies **AC-3**.
- **Disabled.** With RAG disabled, rows stay with `attempts` unchanged and no calls are made. Verifies **AC-8**.
- **Parse failure.** A parse timeout marks the document `failed`, clears its `remote_doc_id`, and creates a `parse_failed` row. Poll retries running out do the same. A retry upload then leaves exactly one remote document. Verifies **AC-9**.
- **Dataset race.** Two concurrent first indexes leave the extra dataset as a `duplicate_dataset` row, which the worker deletes. Verifies **AC-10**.
- **Status.** Two rows, one with an error, give `count: 2`, the older `created_at`, and that error. No rows give zero and nulls. A Stakeholder can read it, and an outsider gets 403. Verifies **AC-11**.
- **Script.**
  - On the JSON store, `--pending-deletes` lists a deleted project's dataset row, makes no call, and exits 1.
  - With `--run` it deletes the row, prints `deleted 1, still pending 0`, and exits 0.
  - Against a fake that always fails, it returns at once (no sleeps), records one attempt, and exits 1.

  Verifies **AC-12**.
- **PostgreSQL.** The migration applies. A duplicate insert hits the partial unique index, rolls back, and returns the existing row, and the session is usable afterwards. Verifies **AC-2**.

## Build plan

Built Tracer Bullet style: slice 1 carries one document delete through the table, the worker, and a restart, on both stores, before the other paths join.

**Slice 1: one document delete survives a restart**
1. [x] Add `KnowledgeRemoteDelete`, the five repository methods on the interface and the JSON store (under `_write_lock`, with the AC-2 rules), `KnowledgeRemoteDeleteDB` with its indexes, and the Alembic revision. Satisfies **AC-1**, **AC-2**.
2. [x] Add `RemoteNotFound` and the `status_code` and `code` fields on `RetrieverError` (filled by `_call`). Make the `RAGFlowRetriever.delete` short count path check with a GET, and match the dataset not found answer in `delete_dataset` after checking it against the installed RAGFlow. `InMemoryRetriever` raises `RemoteNotFound` for unknown IDs. Satisfies **AC-13**.
3. [x] In the indexer, add:
   - the row backed `enqueue_remote_delete`, with `reason`, one row per ID, `row_id` in the job, and the AC-3 fallback
   - `_queued_deletes`, with the ID freed in `_run`'s `finally`
   - the shared `_run_delete_row` handler
   - rows in `sweep()`

   Wire `KnowledgeService.delete` with `reason="document_deleted"`. Satisfies **AC-1**, **AC-3**, **AC-4**, **AC-5**, **AC-6**, **AC-7**.

**Slice 2: every delete path, and the resweep**
4. [x] Do the same for datasets: row backed `enqueue_dataset_delete`, the row aware `_delete_dataset`, and the absorb rule in the worker. Wire `project_deleted`. Satisfies **AC-1**, **AC-2**, **AC-4**.
5. [x] Pass a `reason` from `_remove_entry`, the `_index_source` replacement, and the upload race path. Add the AC-9 cleanup to `_fail`, so every failure path gets it. Switch `_ensure_dataset` to `enqueue_dataset_delete(..., reason="duplicate_dataset")`. Satisfies **AC-1**, **AC-9**, **AC-10**.
6. [x] Add the `RAG_DELETE_RESWEEP_MINUTES` setting, the `JOB_RESWEEP` job scheduled from `start()`, and the disabled mode behavior. Satisfies **AC-7**, **AC-8**.

**Slice 3: visibility**
7. [x] Add `pending_deletes` to `KnowledgeService.status`, and to the route's response schema if it has one. Satisfies **AC-11**.
8. [x] Add `--pending-deletes [--run]` to `reindex_project_knowledge`: listing, the JSON store guard, one attempt per row through `_run_delete_row(retries=False)`, and the exit code. Satisfies **AC-12**.
9. [x] Update the docs:
   - `docs/api.md`: the status field
   - `docs/architecture.md`: the pending delete table and resweep
   - README and `.env.example`: the setting
   - spec 0006: the Consequences line about the in memory queue

   Satisfies **AC-11**, **AC-7**.

## Consequences

**Positive**:
- Deleted project data is removed from RAGFlow even after an outage or restart, which closes the review's Major finding.
- Retrying forever is safe and needs no human once the cause is fixed.
- The parse failure and dataset race leaks are closed by the same mechanism.

**Negative / tradeoffs**:
- One more table, migration, and set of repository methods across both stores.
- A row that can never succeed (for example after RAGFlow moves to a new server whose datasets are different) is retried every 30 minutes forever and logs a warning each time, until someone removes it by hand. The status route and script show it, but nothing removes it automatically.
- A crash between the local delete and the row save still leaks one remote item. This is accepted rather than adding a transaction the JSON store cannot offer.
- One RAGFlow call per row, instead of batching, means more calls after a large delete (for example a regeneration that replaces many test cases).

**Neutral**:
- `enqueue_remote_delete` and `enqueue_dataset_delete` gain a required `reason`, so their six call sites change.
- On the JSON store, running deletes from the script means stopping the server first. That is the cost of a file store with only an in process lock.
- The status response gains one field, and the UI ignores it until a Follow-up shows it.

## Follow-up

- [ ] Track the remote document when `_start_parse` fails after a successful upload. Today the remote ID is lost inside `upload_file`. Carrying it on the raised `RetrieverError` would let the indexer queue a delete.
- [ ] A remote delete is not queued when `KnowledgeService.delete` reads `rag_dataset_id` as empty while a first index is creating the dataset at the same moment (from 0006, unchanged). It is accepted as rare; the orphan report below would catch it.
- [ ] Report RAGFlow datasets named `proj-<id>` that no live project owns (orphans from before this spec, or from a crash in the AC-1 gap). This needs a "list datasets" call that the retriever does not have yet.
- [ ] Show `pending_deletes` on the Knowledge Base screen.
- [ ] Consider a way to drop a row by hand, for example `--pending-deletes --drop <id>`, if rows that can never succeed show up in practice.
