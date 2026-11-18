# 0006. Give each project its own knowledge base and add agentic retrieval to chat and the workflow

**Date**: 2026-11-17 · updated 2026-11-18 (Jira stories and defects moved to spec [0008](../0008-jira-knowledge-sources/index.md); failed runs indexed from every save path, task 12b)
**Status**: Accepted

## Structure

- [0007. Persist pending RAGFlow deletes](0007-persist-pending-ragflow-deletes.md): saves every remote document and dataset delete in a `KnowledgeRemoteDelete` table and retries it at startup and every 30 minutes until it succeeds. Supports AC-5 and AC-10 (remote cleanup that survives outages and restarts).

## Summary

Each project gets its own RAGFlow dataset (its own searchable knowledge base), so documents never mix across projects. Upload now sends the real file to the server; today only the file name is sent and placeholder text gets indexed. The chat assistant gains a `search_knowledge` tool. A new workflow stage, `knowledge_retrieval_agent`, searches the project's knowledge, has an LLM grade what comes back, and rewrites the query and tries again when the results are weak. The scenario, test case, requirement analyst, execution analysis, and defect agents then use what it found. Retrieval quality is measured (recall and groundedness) and traced in LangSmith.

## Requirements

**User stories**:
- As a QA engineer, I want to upload product documents to my project, so that generated scenarios and test cases reflect how the product actually works.
- As a QA engineer, I want my project's documents kept apart from every other project's, so that nobody sees, retrieves, or deletes another team's files.
- As a QA engineer, I want to ask the chat assistant what our documents say, so that I can get answers without opening the files.
- As a QA lead, I want to know whether retrieval finds the right material and whether generated test cases are backed by it, so that I can trust the output.

**Acceptance criteria**:

*Per project knowledge base and upload*
- **AC-1**: `POST /api/v1/projects/{project_id}/documents/upload` accepts one multipart file of type PDF, DOCX, TXT, MD, CSV, or XLSX up to `RAG_UPLOAD_MAX_MB` (default 20). It saves the bytes to `<RAG_STORAGE_ROOT>/<project_id>/documents/<document_id>/<sanitized name>`, creates a `Document` with `embedding_status` `pending` and that real `storage_path`, queues indexing, and returns 201 with the document right away.
  - **Type check.** The type comes from the file extension. The content type the browser sends is only a hint and never causes a rejection on its own.
  - **Errors.** A file that is too large returns 413; the check runs while the file streams in, so the whole file is never held in memory. A disallowed extension returns 415. An empty file returns 422.
  - **Names.** `Document.filename` holds the sanitized name and `original_filename` holds the user's name. The sanitized name has no path separators or `..`, no Windows reserved names (`CON`, `NUL`, `COM1` and the like), no trailing dots or spaces, and is at most 120 characters.
  - **Duplicates.** The same file name may be uploaded more than once; each upload gets its own `document_id` folder.
- **AC-2**: The first time anything is indexed for a project, the indexer creates a RAGFlow dataset named `proj-<project_id>` and saves its ID to `Project.rag_dataset_id`. Every later index, search, and delete for that project uses only that dataset. Two concurrent first indexes for one project create exactly one dataset. A per project lock in the indexer covers this, and the ID is saved through `set_project_rag_dataset(project_id, dataset_id)`, which writes only when the field is still empty. If another writer got there first, the extra RAGFlow dataset is deleted and the saved one is used. Running more than one server process is not supported by this spec. There is no global dataset: `RAGFLOW_DATASET_ID` and `rag.ragflow_dataset_id` are removed, and `rag.vector_db_provider` must be `ragflow` for the retriever to be active (the same check `rag_service.is_active()` makes today).
- **AC-3**: A document's `embedding_status` (the existing field, with its existing value names) moves `pending → processing → completed | failed`. The indexer uploads the file, saves the RAGFlow document ID to `Document.remote_doc_id`, starts parsing, and polls RAGFlow's parse status every `RAG_PARSE_POLL_SECONDS` (default 5). It becomes `completed` only when RAGFlow reports that parsing succeeded. If parsing fails, or does not finish within `RAG_PARSE_TIMEOUT_SECONDS` (default 600), it becomes `failed`, with RAGFlow's message or "parsing timed out" in `Document.error`.
- **AC-4**: When a RAGFlow call fails (connection error, timeout, or a 5xx response), the indexer retries after 10 s, 60 s, and 300 s (`RAG_INDEX_RETRY_DELAYS`), then marks the item `failed` with the last error. The indexer never writes placeholder content. If the saved file is missing, the document is marked `failed` with "file not found" and is not retried. On startup, the app queues every document still `pending` or `processing` again (when RAG is enabled), so a restart does not leave documents stuck.
- **AC-5**: `DELETE /api/v1/projects/{project_id}/documents/{document_id}` checks project access and permission before looking the document up, so a document in another project gets the same 404 as one that does not exist. It captures `remote_doc_id` and `project.rag_dataset_id` first, deletes the record and the local file, then queues deletion of that remote document from that dataset only. Deleting a document in project B never touches a file with the same name in project A. If the remote delete fails, it is logged and the local delete still succeeds. If the indexer finishes an upload for a document that was deleted in the meantime, it deletes the new remote document instead of saving it.
- **AC-6**: The knowledge routes require a signed in user (401 otherwise), project access through `require_project_access` (403), and the `Knowledge` permission: `view` to list and to read status, `create` to upload, and `delete` to delete (403 without it). The old JSON only `POST /api/v1/projects/{project_id}/documents` route is removed. The `Knowledge` module is added to the seeder, which already runs at every startup and only adds missing rows, so existing databases get the grants on their next restart.
- **AC-7**: `GET /api/v1/projects/{project_id}/knowledge/status` returns:
  - `enabled`
  - `dataset_id` (or null)
  - document counts by status
  - knowledge entry counts by `source_type` and status
  - `last_indexed_at`
- **AC-8**: When `RAG_ENABLED` is false, upload still saves the file and the record with `embedding_status` `pending`, and `error` reads "knowledge base is disabled". Nothing calls RAGFlow. The document stays `pending` until RAG is enabled; then the startup sweep (AC-4) or the reindex script picks it up. This is expected, not a fault.
- **AC-9**: A one time data migration marks every document created before this spec as `failed` with the error "file was never uploaded, please upload it again". Their records stay visible.
  - In PostgreSQL it is part of the slice 2 Alembic migration.
  - In the JSON store it runs once at startup and writes a marker (`knowledge_migration_0006: done`) so it never runs twice.
- **AC-10**: Deleting a project (a soft delete) removes its `<RAG_STORAGE_ROOT>/<project_id>/` folder right away. It also queues a best effort deletion of its RAGFlow dataset, using the dataset ID read before the delete. This happens in the project delete route, not the repository. Failures are logged and never block the project delete.
- **AC-11**: The Knowledge Base screen uploads the real file through a new `API.uploadDocument(projectId, file)`, which passes a `FormData` body straight to the shared `request` helper (it already leaves the content type to the browser for `FormData`). `API.createDocument` and the fake progress bar are removed, and the screen shows an "Uploading…" state until the 201 arrives. Each document shows its status and, when it has failed, its error. While any document is `pending` or `processing`, a `setInterval` refreshes the list every 5 s. It is cleared when none are left and when you leave the screen.

*Chat search tool*
- **AC-12**: The chat toolbox offers `search_knowledge(query, source_types?, top_k?)`:
  - `query` is required.
  - `source_types` is any of `document`, `jira_story`, `test_case`, `execution_failure`, `defect`, `locator`.
  - `top_k` is 1 to 10, default 5.

  It returns `items` of `{text, score, source_type, source_ref, title}`, drawn only from the project's own dataset and only for chunks that map to a local `Document` or `KnowledgeEntry` of this project. Any user with project access can use it, like the other read tools. With RAG disabled, or with no dataset yet, it returns `{"error": "the knowledge base is not set up for this project"}`. `ChatToolbox` receives `KnowledgeService` by injection, the same way it receives the repository. Each item's `text` is cut to 600 characters before the result goes through `fit_result`, so several items fit inside the chat tool result cap.
- **AC-13**: The chat system prompt tells the assistant three things:
  - use `search_knowledge` for questions about product behavior or document content
  - name the source titles it relied on
  - when results look weak, rephrase and search once more within the existing tool step limit

*Indexing other project sources*
- **AC-14**: These are indexed as `KnowledgeEntry` rows, one per `(project_id, source_type, source_ref)`:
  - approved test cases (`test_case`)
  - failed executions (`execution_failure`), from every path that saves a run result: the UI run through the workspace webhook, the batch run, and the Python graph run. The entry holds the failure category and analysis summary when the run has them. Today only the graph run is analysed, so UI and batch entries hold the test case title and error
  - object repository pages with their locators (`locator`)

  Content with the same `content_hash` is not sent again. Changed content replaces the old remote document. When a source is deleted, its entry and remote document are removed. Jira stories (`jira_story`) and Jira defects (`defect`) follow the same entry rules and are specified in spec [0008](../0008-jira-knowledge-sources/index.md) (its AC-1).
- **AC-14a**: Moved to spec [0008](../0008-jira-knowledge-sources/index.md) (its AC-2), on 2026-11-18, so this spec can be verified without a working Jira.
- **AC-15**: Indexing hooks only enqueue `(project_id, kind, ref)` onto the background indexer, and run after the save succeeds. They never slow down, block, or fail the save that triggered them. They are called from services and routes, never from repositories, so they behave the same under JSON and PostgreSQL. The call sites are listed in *Feature design > Indexing hooks*. When a job runs, the worker reads the project and the source again, so it indexes current content. If the source is gone or soft deleted, the worker removes its entry instead.
- **AC-16**: `python -m backend.scripts.reindex_project_knowledge --project <id>` (or `--all`) indexes every current source of a project, plus its `pending` documents, through the same indexer, and prints counts of indexed, skipped (unchanged), and failed items. Its Jira story and defect part is spec [0008](../0008-jira-knowledge-sources/index.md) AC-3.

*Retrieval stage in the workflow*
- **AC-17**: When `RAG_WORKFLOW_RETRIEVAL_ENABLED` is true, a retrieval node runs:
  - before `scenario_agent` on every path that reaches it (`GENERATE_SCENARIOS`, the no operation fallback, and after `qa_story_analyzer_agent`)
  - before `test_case_agent` (`GENERATE_TESTCASES`, and after `human_approval_agent_1`)
  - before `requirement_analyst_agent` (`INGEST`)
  - before `execution_analysis_agent` (after `execution_agent`)

  When the flag is false, the graph has the same order as today. The flag is read every time a route runs, not once when the graph is built, because a module level `graph` is built at import time.
- **AC-18**: Each retrieval stage searches a fixed set of source types:

  | Stage | Source types |
  |---|---|
  | `requirements` and `scenarios` | `document`, `jira_story` |
  | `test_cases` | `document`, `jira_story`, `test_case`, `locator` |
  | `analysis` | `execution_failure`, `defect`, `test_case`, `locator` |

  Queries:
  - **Other stages:** built from the requirement's title, description, and acceptance criteria, whichever are present. When only the title exists, the title alone is the query.
  - **`analysis`:** built from up to 5 failed `execution_results`, each contributing its test case title and `error_message`. The title comes from `state.generated_test_cases`, or from the repository by `test_case_id`. The requirement title is added when a requirement is on the state; the execution route builds the state without one, so it is optional.
  - **Empty query:** if the query comes out empty (for example, no failed executions), the stage skips.
- **AC-19**: On each attempt, up to `RAG_MAX_RETRIEVAL_ATTEMPTS` (default 2):
  1. Search with `top_k` = `RAG_TOP_K` (default 8).
  2. Grade every chunk in one batched LLM call, on the `retrieval` role.
  3. Keep the chunks graded relevant.
  4. If fewer than `RAG_MIN_RELEVANT_CHUNKS` (default 2) relevant chunks have been collected and attempts remain, ask the LLM to rewrite the query, then search again.

  Chunks are deduplicated across attempts by chunk ID. The final `retrieved_context` is sorted by score and capped at `RAG_TOP_K`. The agent sets these fields, and no others:
  - `retrieval_status`: `ok`, `empty` (searched, nothing relevant), or `skipped` (did not search, with the reason logged)
  - `retrieval_query`: the last query used
  - `retrieval_attempts`
  - `retrieval_sources`: the source types searched
  - `retrieved_context`

  It loads the project with `get_project_repository().get_project(state.project_id)`.
- **AC-20**: The retrieval stage never fails the workflow. It logs, sets `retrieval_status` to `skipped`, leaves `retrieved_context` empty, and returns the state in any of these cases:
  - RAG is disabled
  - `state.project_id` is missing
  - the project has no dataset
  - the query is empty
  - RAGFlow errors or times out
  - the retrieval role is busy past its max wait

  If the grader's output cannot be parsed, it keeps the top `RAG_MIN_RELEVANT_CHUNKS` chunks by score, marked `graded: false`, and logs "grading failed". The grade and rewrite prompts treat chunk text as data. The grader must answer in JSON only (`{"relevant": [chunk ids]}`) and the rewriter in plain text only, and nothing a chunk says changes which calls are made.
- **AC-20a**: Every place that creates a `WorkflowState` for a project sets `project_id`:
  - `graph/workflow.py` `run_workflow`
  - `WorkflowService` (`ingest_requirement` and its other operations)
  - the routes at `main.py` that build state for test case generation and execution analysis
  - `grounded_playwright_service`

  Today none of them set it.
- **AC-21**: `scenario_agent`, `test_case_agent`, `requirement_analyst_agent`, `execution_analysis_agent`, and `defect_management_agent` each add a "Project knowledge" block to their prompt when `retrieved_context` is not empty. The block is delimited, each chunk is tagged with `[source_type:source_ref]`, and it is labelled as reference data, not instructions. When the context is empty, their prompts are exactly as before. `defect_management_agent` reuses the `analysis` stage context.
- **AC-22**: The grade and rewrite calls run on a new `retrieval` LLM role. Token usage is attributed to agent `knowledge_retrieval_agent`. Each unset `RETRIEVAL_LLM_*` value falls back to the resolved analysis role, the same way the chat role does (spec 0005). Pool defaults are max concurrent 2, cooldown 30 s, and max wait 20 s.
- **AC-23**: `FeatureInventoryAgent` searches through the same retriever and per project dataset. `RAGFlowService` is deleted.

*Measure and trace*
- **AC-24**: When LangSmith is enabled, every search, grade, and rewrite inside the retrieval stage records a span with `langsmith.traceable`, using the same guarded import fallback `graph/workflow.py` uses. Each span carries this metadata:
  - stage
  - attempt
  - query
  - source types
  - returned `source_ref`s
  - relevant count

  When it is disabled, nothing is sent and nothing fails.
- **AC-25**: `RetrievalEvalService.evaluate(retriever, project, cases, k)` returns recall@k and MRR (mean reciprocal rank: on average, how high the first correct result ranks), per case and overall, for a list of `{query, expected_source_refs, source_types?}`. A case's recall@k is the fraction of its expected refs found in the top k. Its reciprocal rank is 1 divided by the rank of the first expected ref, or 0 if none appears. A pytest test runs it against the in memory retriever and a fixture corpus. `python -m backend.scripts.eval_retrieval --project <id> --cases <file.json>` runs it live and prints the metrics.
- **AC-26**: When `retrieved_context` is not empty, `evaluation_agent` scores each generated test case's steps and expected result for support by that context. It sets `TestCase.groundedness_score` (0 to 1) and `TestCase.unsupported_claims` (a list of short claims). When the context is empty, it leaves them as `None` and `[]`. Scoring is one batched call per evaluation run on the `analysis` role. If that call fails, finds the pool busy, or returns output that cannot be parsed, the fields stay `None` and `[]` and a warning is logged. Groundedness never changes `evaluation_status`; it is reported, not enforced.

## Decision

**Chosen option**: Option 1: Fix in place with one RAGFlow dataset per project behind a `Retriever` interface

You keep RAGFlow, give every project its own dataset, and put one `Retriever` interface in front of it. Chat and workflow agents use that interface, and tests use an in memory fake. A graded, self correcting retrieval stage feeds the agents. Reasoning and the options weighed are in [rationale.md](rationale.md).

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

**Data model sketch** (target; each migration lands in the slice that needs it):

| Entity | Change | Fields | Keys and rules |
|---|---|---|---|
| `Project` | add | `rag_dataset_id: str \| None` | Set once by the indexer. Never shared. |
| `Document` | add | `remote_doc_id: str \| None`, `error: str \| None` | FK `project_id → Project` (1:N, unchanged). `storage_path` is the real saved path. |
| `KnowledgeEntry` | new | `id: UUID` PK, `project_id: UUID` FK, `source_type: str` (`jira_story`, `test_case`, `execution_failure`, `defect`, `locator`), `source_ref: str`, `title: str`, `remote_doc_id: str \| None`, `content_hash: str` (sha256 of the indexed text), `status: str` (`indexed`, `failed`), `error: str \| None`, `indexed_at: datetime` | Unique `(project_id, source_type, source_ref)`. Index on `(project_id, remote_doc_id)` for the search mapping. 1:N from `Project`. |
| `TestCase` | add | `groundedness_score: float \| None`, `unsupported_claims: list[str]` default `[]` | Set only by `evaluation_agent`. |
| `WorkflowState` | add (not persisted) | `retrieval_status: str \| None` (`ok`, `empty`, `skipped`), `retrieval_query: str \| None`, `retrieval_attempts: int = 0`, `retrieval_sources: list[str] = []` (source types searched), `retrieved_context: list[dict] = []` | Owned only by `knowledge_retrieval_agent`. Each context item is `{chunk_id, text, score, source_type, source_ref, title, graded: bool}`. |

`KnowledgeEntry` gets the same three layers as other entities: a domain model in `backend/models/knowledge.py`, methods on the `ProjectRepository` interface with JSON and PostgreSQL implementations, and a SQLAlchemy table plus an Alembic migration. Repository methods:
- `get_knowledge_entry(project_id, source_type, source_ref)`
- `save_knowledge_entry`
- `delete_knowledge_entry`
- `list_knowledge_entries(project_id)`
- `find_by_remote_ids(project_id, remote_ids)`

The same `find_by_remote_ids` lookup is needed for documents. One more repository method is needed: `set_project_rag_dataset(project_id, dataset_id) -> str`. It writes only when the field is empty and returns the stored value. It never saves a whole `Project` from a stale copy, because that could overwrite other fields. The `TestCase` fields are stored as new columns in PostgreSQL and as new keys in the JSON test case record.

**State transitions**:
- Document `embedding_status`: `pending → processing → completed`; `pending | processing → failed`. The unused `vector_collection` field is left as it is. Deleting a document is allowed in any state.
- KnowledgeEntry: `indexed`, or `failed` after the retries run out. A changed hash sends the entry through indexing again, and it ends in either state.

**Components** (all new code lives in services or agents; routes stay thin):

| Component | Path | Responsibility |
|---|---|---|
| `Chunk`, `Retriever` protocol, `IndexResult` | `backend/services/retrieval/base.py` | The contract. `ensure_dataset(project)`, `upload_file(project, path, name)`, `upload_text(project, text, name)`, `parse_status(project, remote_id)`, `delete(project, remote_ids)`, `delete_dataset(project)`, `search(project, query, top_k)`, `is_active()`. |
| `RAGFlowRetriever` | `backend/services/retrieval/ragflow.py` | The only RAGFlow HTTP client (`requests`, timeouts, `logging`). Moved out of `rag_service.py`. |
| `InMemoryRetriever`, `NullRetriever` | `backend/services/retrieval/fake.py` | Keyword overlap search for tests; `NullRetriever` is inactive and returns nothing. |
| `get_retriever()` | `backend/services/retrieval/factory.py` | Returns `RAGFlowRetriever` when `RAG_ENABLED`, otherwise `NullRetriever`. |
| `KnowledgeService` | `backend/services/knowledge_service.py` | Upload, delete, status, `search(project, query, top_k, source_types)` (local post filter and source mapping), project cleanup. |
| `KnowledgeIndexer` | `backend/services/knowledge_indexer.py` | One daemon worker with a queue (the token usage writer pattern). Queue items carry IDs only: `(project_id, kind, ref)`. It handles dataset creation under a per project lock, upload, parse polling, retries, hash skip, and replacement. Hooks call `enqueue_document`, `enqueue_source`, `enqueue_source_delete`, `enqueue_remote_delete`, and `enqueue_dataset_delete`. When PostgreSQL is active, it calls `db_session.remove()` after every repository step and never holds a session open while it sleeps between polls or retries. Retry waits are scheduled, not slept, so one stuck job does not block the queue. A sleeper and clock are injected for tests. It starts with the app and runs the startup sweep (AC-4). |
| `KnowledgeRetrievalAgent` | `backend/agents/knowledge_retrieval_agent.py` | The graded retrieval loop. Prompts live in `backend/prompts/agents/knowledge_retrieval/` (`grade.md`, `rewrite.md`). |
| `RetrievalEvalService` | `backend/services/retrieval_eval_service.py` | recall@k and MRR. |

**Search mapping** (the local post filter, decided in the design conversation):
1. Search the project's dataset with `top_k × 3`.
2. Map each chunk's RAGFlow document ID through `find_by_remote_ids` on documents and knowledge entries. Drop any chunk that does not map.
3. Filter by `source_types`.
4. Keep the top `top_k` by score.

**Graph wiring**: register four retrieval nodes that share one retriever and one LLM service:
- `retrieval_requirements`
- `retrieval_scenarios`
- `retrieval_test_cases`
- `retrieval_analysis`

Each is a `KnowledgeRetrievalAgent(stage=...)`. Add the four names to `routing_map`, and give each a fixed edge to its consumer:

| Retrieval node | Consumer |
|---|---|
| `retrieval_requirements` | `requirement_analyst_agent` |
| `retrieval_scenarios` | `scenario_agent` |
| `retrieval_test_cases` | `test_case_agent` |
| `retrieval_analysis` | `execution_analysis_agent` |

Two kinds of entry point need changing.

- **Supervisor routes.** A helper `SupervisorAgent._via_retrieval(target)` returns the matching retrieval node when the flag is on, or `target` itself when it is off. It reads settings on every call. Use it in:
  - `route_start`, where it returns `requirement_analyst_agent`, `scenario_agent` (both the operation and the no operation fallback), and `test_case_agent`
  - `route_after_human_approval_1`, where it returns `test_case_agent`
- **Fixed edges.** Two are plain `add_edge` calls today, not supervisor routes: `qa_story_analyzer_agent → scenario_agent` and `execution_agent → execution_analysis_agent`. Replace each with `add_conditional_edges` and a small router that uses the same helper.

`build_graph` gains a `knowledge_retrieval_agents: dict[str, BaseAgent] | None` parameter for injection. When the flag is off, every router returns the consumer directly. The nodes stay registered but are never reached. `defect_management_agent` runs after `execution_analysis_agent` and reads the same state, so it needs no node of its own.

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/v1/projects/{project_id}/documents/upload` | POST | `file` (multipart, required) | 201 `Document` | bearer, project access, `Knowledge:create` | 401, 403, 404 project, 413, 415, 422 |
| `/api/v1/projects/{project_id}/documents` | GET | none | `list[Document]` | bearer, project access, `Knowledge:view` | 401, 403, 404 |
| `/api/v1/projects/{project_id}/documents/{document_id}` | DELETE | none | `{"detail": ...}` | bearer, project access, `Knowledge:delete` | 401, 403, 404 |
| `/api/v1/projects/{project_id}/knowledge/status` | GET | none | `{enabled, dataset_id, documents: {status: n}, entries: {source_type: {status: n}}, last_indexed_at}` | bearer, project access, `Knowledge:view` | 401, 403, 404 |
| `/api/v1/projects/{project_id}/documents` | POST (JSON) | removed | | | 405 |
| chat tool `search_knowledge` | tool call | `query` (req), `source_types`, `top_k` | `{items: [...]}` or `{error}` | project access (chat) | not found in this project |

`Document` responses gain `remote_doc_id` and `error`. They never include the API key or server paths beyond the existing `storage_path`.

**RAGFlow calls** (HTTP API v1, through `RAGFlowRetriever` only):
- create dataset: `POST /api/v1/datasets` with `{name, parse_type: 1, parser_id: "general"}` (RAGFlow v1.0 requires `parse_type`; 1 is the BuiltIn parser, which needs `parser_id`. The older `chunk_method` field is rejected.)
- upload: `POST /api/v1/datasets/{id}/documents` (multipart)
- start parsing: `POST /api/v1/datasets/{id}/chunks` with `{document_ids}`
- parse status: `GET /api/v1/datasets/{id}/documents?id=`, reading the run status
- delete documents: `DELETE /api/v1/datasets/{id}/documents` with `{ids}`
- delete dataset: `DELETE /api/v1/datasets` with `{ids}`
- retrieval: `POST /api/v1/retrieval` with `{question, dataset_ids: [id], top_k}`

Text sources upload as a `.txt` file named `<source_type>__<source_ref>.txt`. The chunk's RAGFlow document ID is read from the retrieval response field `document_id`; check the exact name against the installed version, as noted in the Follow-up.

**Indexing hooks** (each runs after the save succeeds and only enqueues work):

| `source_type` | `source_ref` | `title` | Enqueue on | Remove on |
|---|---|---|---|---|
| `test_case` | test case ID | test case title | the test case edit route (the approve and reject buttons use it), the chat update and review actions, and `WorkflowService` test case and test data generation. Only `approved` cases stay indexed | test case delete route, cases replaced by regeneration, the chat requirement delete |
| `jira_story` | Jira issue key | story summary | Jira import route (`import_jira_story`), after the analyst pipeline. (`jira_sync_service` only pushes scenarios to Jira and never changes a story locally, so it is not a hook) | the chat requirement delete, when the requirement came from that story |
| `execution_failure` | execution ID | test case title + " failed" | for failed and errored runs only, immediately after the result is saved, on all three save paths: `ExecutionService.finalize_execution` (the workspace webhook, which is what the UI Run button uses), `ExecutionService.start_batch_run` (once per saved batch result; chat batch runs come through here too), and `run_execution_and_stream` (after the analysed result is saved). The Jira sync resave of an existing run needs no hook | execution delete route and `DELETE /executions/failed` |
| `defect` | Jira bug key | Jira summary (the key when empty) | `run_execution_and_stream` when the saved result has a `jira_bug_id`; `jira_sync_service.sync_execution_finished` after it saves the result; the sync bug route, after the graph returns, for the keys on the execution and test case read back from the repository; the Jira webhook, before its resolved status filter, for every project with a live execution or test case linked to the key (one job per project, from `list_projects()`) | every delete that can drop a link reads the keys first, then queues them after the delete completes: the execution delete route (`get_execution_result` before the delete), `DELETE /executions/failed` (keys of the rows it actually deleted, captured in its existing pass), the test case delete route, test cases replaced by regeneration, and the chat requirement delete (keys of its test cases). The worker removes the entry when no link is left (AC-14a). A closed bug is never removed |
| `locator` | object repository page ID | page name | page create and edit routes, and element create, edit, and delete routes (an element's page is indexed again) | page delete route |

Saves made inside other agents are not hooked, except where this table names them. The reindex script covers anything missed.

**Defect source** (decided 2026-11-17; governed by spec [0008](../0008-jira-knowledge-sources/index.md) AC-2 since 2026-11-18, along with the `jira_story` and `defect` rows of the hooks table above):
- Defects exist only in Jira. Locally the app keeps only the bug key: `ExecutionResult.jira_bug_id` (set by `defect_management_agent` and `jira_sync_service`) and `TestCase.jira_issue_key` (set by those two and by `jira_sync_agent`'s sync bug path, which saves the key on the test case only). Every writer of `TestCase.jira_issue_key` stores a bug key, never a story key.
- The source reader takes an injected `JiraService`, so tests never call Jira. It checks `mock_mode` on that instance *before* calling `get_issue`, because mock mode returns made up text. `mock_mode` is always true under pytest, so the test fake sets `mock_mode = False`. It distinguishes three outcomes. A read that returns the issue gives a `SourceText`. A key with no live local link gives `None` (the indexer removes the entry). A Jira read that fails, or a service in mock mode, raises `SourceUnavailable(message, transient)`. The indexer retries a transient one like a RAGFlow failure and skips a non transient one (mock mode), counting it as skipped and writing nothing.
- `JiraService.get_issue` returns `None` both for a missing issue and for a network error, so the reader treats `None` as a transient failure, never as "gone". Only the local links decide removal.
- Text, one item per line joined with `\n`, so the same issue always gives the same `content_hash`: `Defect <key>: <summary>`, `Status: <status.name>`, `Priority: <priority.name>`, then `Description:` and the description cut to its first 4000 characters. Lines with an empty value are left out. Title: the summary, or the key when it is empty.
- Field shapes: a missing `fields`, `status`, or `priority`, or a `None` value, becomes an empty string, never an error. A string `description` is used as is. A rich text description (the Atlassian document format, a nested object) has the `text` values of its nodes joined with spaces.
- The reindex script's defect keys are the deduplicated union of the trimmed, non empty `jira_bug_id` of live executions and `jira_issue_key` of live test cases. `--all` makes one Jira call per bug key and per project, with no extra rate limit beyond `JiraService`'s own retry adapter. This is accepted, because the script is an occasional backfill.
- Ordering: every hook queues after its save or delete has completed, and the worker reads links and Jira when the job runs. So a webhook job racing a delete still ends correct. The link check and the entry write are not one atomic step, and the queued job that runs last settles it.

**Value sourcing**:

| Action | Value produced or displayed | Source |
|---|---|---|
| upload | `storage_path` | derived: `RAG_STORAGE_ROOT` + `project_id` (path) + new `document_id` + sanitized `file.filename` |
| upload | `mime_type`, `size` | `UploadFile.content_type`, byte count while saving |
| upload | allowed type | file extension checked against `RAG_UPLOAD_TYPES`, and the content type must not contradict it |
| indexer | dataset to use | `Project.rag_dataset_id`, created on first use (AC-2) |
| indexer | `remote_doc_id` | the RAGFlow upload response `data[0].id` |
| indexer | completed or failed | RAGFlow parse run status from the polling call |
| indexer | `content_hash` | sha256 of the exact text uploaded for the source |
| indexer | source text | built per source type by `knowledge_indexer` formatters: test case title, steps, and expected result; story key, summary, description, and acceptance criteria; execution test case title, error, and, only when the saved result has them, failure category and analysis summary (`ExecutionResult.failure_category`, `root_cause_summary`; empty for UI and batch runs today); defect key, summary, status, priority, and description read from Jira (AC-14a); page name with its element names and locators |
| indexer | defect belongs to the project | a live `ExecutionResult.jira_bug_id` or live `TestCase.jira_issue_key` of this project equal to the key (AC-14a) |
| status | `last_indexed_at` | max of `KnowledgeEntry.indexed_at` and the document `uploaded_at` of completed documents |
| search | `source_type`, `source_ref`, `title` | the `Document` (`document`, document ID, `original_filename`) or `KnowledgeEntry` row matched by `remote_doc_id`; per source values in *Indexing hooks* |
| retrieval stage | the project | `get_project_repository().get_project(state.project_id)`; `project_id` is set by every state creator (AC-20a) |
| indexer | current source content | the worker reads the source again from its repository by `ref` when the job runs |
| reindex script | all sources | `get_test_cases_for_project`, `get_requirements` (Jira linked), `get_execution_results` (failed, not deleted), the distinct bug keys on the project's live executions (`jira_bug_id`) and test cases (`jira_issue_key`), the object repository store's project pages; soft deleted items are skipped |
| search | `score` | RAGFlow chunk `similarity` |
| retrieval stage | stage | node constructor `stage` argument |
| retrieval stage | query | derived from `state.requirement`, plus failed `state.execution_results` for `analysis` |
| retrieval stage | relevant flag | grader output; when it cannot be parsed, the fallback in AC-20 |
| groundedness | score and claims | the LLM judge on the `analysis` role, given the test case and `state.retrieved_context` |
| project delete | dataset to delete | `Project.rag_dataset_id`, read before the project row is soft deleted |

**Key invariants**:
- Every retriever call takes a `Project` and uses only `project.rag_dataset_id`. No call accepts a raw dataset ID from a route, the chat, or settings.
- A chunk is returned only if it maps to a local record of the same project.
- No code path writes generated or placeholder text into a document file.
- Retrieved text is always placed in prompts as delimited reference data, never as instructions.
- Only `knowledge_retrieval_agent` writes the four retrieval fields on `WorkflowState`.
- The RAGFlow API key stays in `.env` and is never returned by any route.
  - The settings payload stops returning `ragflow_api_key`, and returns `ragflow_api_key_set: bool` instead.
  - Saving settings still accepts `ragflow_api_key` as write only; an empty value keeps the current key.
  - The settings form changes to match: it shows "key is set" from `ragflow_api_key_set`, and sends the field only when you type a new key.

**Security model**:
- New permission module `Knowledge` (`view`, `create`, `edit`, `delete`) is added to `MODULES` in `backend/database/db_seeder.py`. Grants match `ObjectRepository`: Admin gets every action, Project Manager gets view, create, edit, and delete, Tester gets view, create, and edit, and Stakeholder gets view.
- Every knowledge route also calls `require_project_access`.
- The chat tool needs project access only (decided), and its scoping comes from the project's own dataset.
- Uploaded files are stored under the project's folder with a sanitized name (path separators and `..` removed, length limited). The original name is kept only in `original_filename`.
- No PII or compliance scope beyond what users upload. Uploads are treated as project data, the same as requirements.

**Configuration required** (in `RAGConfig`, `default_config.yaml`, and `.env.example`):
- `RAG_ENABLED` (exists): master switch.
- `RAGFLOW_API_BASE`, `RAGFLOW_API_KEY` (exist): the RAGFlow server and key.
- `RAGFLOW_DATASET_ID`: **removed**.
- `RAG_STORAGE_ROOT` (default `data/projects`): where uploads are saved.
- `RAG_UPLOAD_MAX_MB` (20) and `RAG_UPLOAD_TYPES` (`pdf,docx,txt,md,csv,xlsx`): the upload limits.
- `RAG_PARSE_POLL_SECONDS` (5), `RAG_PARSE_TIMEOUT_SECONDS` (600), and `RAG_INDEX_RETRY_DELAYS` (`10,60,300`): indexer timing.
- `RAG_TOP_K` (8), `RAG_MAX_RETRIEVAL_ATTEMPTS` (2), and `RAG_MIN_RELEVANT_CHUNKS` (2): the retrieval loop.
- `RAG_WORKFLOW_RETRIEVAL_ENABLED` (default false): turns the workflow retrieval stage on separately from chat search.
- `RETRIEVAL_LLM_PROVIDER`, `RETRIEVAL_LLM_MODEL`, `RETRIEVAL_LLM_API_KEY`, `RETRIEVAL_LLM_API_BASE`, `RETRIEVAL_LLM_MAX_CONCURRENT`, `RETRIEVAL_LLM_COOLDOWN_SECONDS`, and `RETRIEVAL_LLM_MAX_WAIT_SECONDS`: the new role. Add `"retrieval"` to `LLM_ROLES`.

**Critical test scenarios**:
- Happy path: upload a TXT file to project A with the in memory retriever. Check that the dataset is created once, the status reaches `completed`, and `search_knowledge` returns the chunk with `source_type: document` and the file name as title. Verifies **AC-1**, **AC-2**, **AC-3**, **AC-12**.
- Isolation: projects A and B each have `spec.pdf`. Searching in B never returns A's chunks, and deleting B's copy leaves A's. Verifies **AC-5**, **AC-12**.
- Failure: a fake RAGFlow fails twice, then succeeds, so the document completes. A fake that always fails ends `failed` with the error after three retries (an injected clock or sleeper keeps the test fast). Verifies **AC-4**.
- Parse timeout: parse status stays `RUNNING`, so the document ends `failed` with "parsing timed out". Verifies **AC-3**.
- Concurrency: two indexing jobs for a project with no dataset end with one `ensure_dataset` creation. Verifies **AC-2**.
- Auth: no token gives 401. A user not on the project gets 403. A Stakeholder uploading gets 403. Verifies **AC-6**.
- Limits: 21 MB gives 413, `.exe` gives 415, and an empty file gives 422. Verifies **AC-1**.
- Hash skip: indexing the same test case twice sends it once. Editing it replaces the remote document. Verifies **AC-14**.
- Defects, with a fake `JiraService`:
  - a bug key on a live execution of project A is indexed with its Jira summary and status, and searching in project B never returns it
  - deleting the last execution and test case that link the key removes the entry; deleting one of two links keeps it
  - a Jira read that fails (returns `None`) is retried, then the entry is `failed`, and an existing entry is never removed by it
  - mock mode writes no entry, and its text is never uploaded
  - a closed bug stays indexed with the new status
  - JSON store: a soft deleted execution no longer counts as a link; PostgreSQL: the execution delete route still queues the key it read before the delete
  - a webhook for an open to in progress change reindexes the status; a key linked only from an execution is still found
  - a rich text description, a `None` description, and a missing `priority` all index without error, and the same issue twice gives one upload

  Verifies spec 0008 **AC-2** (was 0006 AC-14a).
- Retrieval loop:
  - a relevant result on the first attempt makes one search and no rewrite
  - one relevant chunk triggers a rewrite and a second search, after which it stops at the attempt cap
  - malformed grader JSON triggers the fallback
  - a retriever exception still lets the workflow continue

  Verifies **AC-19**, **AC-20**.
- Graph: with the flag on, the order is supervisor, then `retrieval_scenarios`, then `scenario_agent`. The order is also correct through `qa_story_analyzer_agent` and `execution_agent`. Turning the flag off on an already built graph restores today's order. Verifies **AC-17**.
- Project ID: every state creator sets `project_id`. A state without one makes the stage skip with `retrieval_status: skipped`. Verifies **AC-20**, **AC-20a**.
- Restart: documents left `processing` are queued again on startup. Verifies **AC-4**.
- Delete race: a document deleted while it is uploading ends with no remote document. Verifies **AC-5**.
- Names: `../x.pdf`, `CON.txt`, and `name.` are sanitized, and a `.md` file sent as `application/octet-stream` is accepted. Verifies **AC-1**.
- Prompt: `scenario_agent` with context includes the delimited block. Without context, the prompt is byte for byte unchanged. Verifies **AC-21**.
- Groundedness: with context, the score and claims are set. With none, they stay `None` and `[]`, and `evaluation_status` is unaffected. Verifies **AC-26**.
- Eval: a fixture corpus with known answers gives the expected recall@k and MRR. Verifies **AC-25**.

## Build plan

Built Tracer Bullet style: slice 1 carries one real file end to end (UI, route, indexer, RAGFlow, chat answer) before anything else thickens.

**Slice 1: one file, end to end, into chat**
1. [x] Write the `Retriever` contract, `InMemoryRetriever`, `NullRetriever`, `RAGFlowRetriever` (moved out of `rag_service.py`), and `get_retriever()`. Keep `RAGFlowService` as a thin wrapper over the retriever until slice 5. Satisfies **AC-2**, **AC-23**.
2. [x] Write a migration adding `projects.rag_dataset_id`, `documents.remote_doc_id`, and `documents.error`, plus the model fields and JSON and PostgreSQL repository mappings. Satisfies **AC-2**, **AC-3**.
3. [x] Build `KnowledgeService.upload` and the `KnowledgeIndexer` worker, covering dataset creation under a per project lock with `set_project_rag_dataset`, upload, parse polling, status updates, and starting the worker with the app. Add the multipart upload route, and remove the JSON create route along with `upload_to_ragflow_task`. Satisfies **AC-1**, **AC-2**, **AC-3**, **AC-6**.
4. [x] Replace the fake progress bar with `API.uploadDocument` (`FormData`), the "Uploading…" state, and the status display with polling. Satisfies **AC-11**.
5. [x] Add `KnowledgeService.search` with the local post filter, the `search_knowledge` chat tool with its label and definition, and the chat prompt guidance. Satisfies **AC-12**, **AC-13**.

**Slice 2: make the knowledge base safe and complete**
6. [x] Add the `Knowledge` permission module and seeding, then add `require_project_access` and permission checks to the list, upload, and delete routes. Satisfies **AC-6**.
7. [x] Make delete use `remote_doc_id` in the project's dataset, and remove the local file. Remove `delete_from_ragflow_task`. Satisfies **AC-5**.
8. [x] Add indexer retries with backoff, the missing file failure, the startup sweep, and the delete race check. Add the disabled mode behavior. Satisfies **AC-4**, **AC-5**, **AC-8**.
9. [x] Add the status route. Satisfies **AC-7**.
10. [x] Write a data migration marking old documents failed, and clean up the dataset and folder when a project is deleted. Remove `ragflow_dataset_id` from settings, config, `.env.example`, the settings payload, and the settings form. The payload now returns `ragflow_api_key_set` instead of the key, and the form follows the write only key rule. Satisfies **AC-9**, **AC-10**, **AC-2**.

**Slice 3: index the rest of the project**
11. [x] Write a migration and the model and repository for `KnowledgeEntry`, plus the indexer `enqueue_source` and `enqueue_source_delete` with the hash skip and replacement logic. Satisfies **AC-14**, **AC-15**.
12. [x] Add hooks: test case approval and edit, Jira import, execution analysis completion (failed runs), object repository page and element saves and deletes, source deletes, and the defect hooks (task 12a). Satisfies **AC-14**, **AC-15**.
12a. [x] Add the defect source: the `defect` reader with an injected `JiraService`, the mock mode guard, `SourceUnavailable`, the live link check, and the field shape handling; the indexer handling for it (retry when transient, skip in mock mode); the four enqueue hooks (the webhook moved before its resolved status filter, and the sync bug route reading its records back); key capture before every delete in *Indexing hooks*; and the defect keys in `list_project_sources` for the reindex script. Satisfies **AC-14a**, **AC-15**, **AC-16**.
12b. [x] Add the `execution_failure` hook to the two save paths that lack it, found by `/check verify` on 2026-11-18: `ExecutionService.finalize_execution` (the workspace webhook) and `ExecutionService.start_batch_run`. Both call one small private helper, `ExecutionService._queue_failure_knowledge(project_id, result)`. It imports `queue_knowledge_source` lazily (inside the helper, to avoid an import cycle), and queues `(project_id, "execution_failure", result.id)` only when the status is in `FAILED_EXECUTION_STATUSES` from `knowledge_sources.py` (`failed`, `error`), so the status rule lives in one place. No analysis is added.
    - **Placement.** Call it immediately after `save_execution_result` and before the steps that follow and can raise: object repository markers, harvest, reports, and the completion event. In `start_batch_run` that is inside the per item loop, so a later error never costs an item its entry.
    - **Re-posts.** A webhook re-post reuses the same execution ID. The unique `(project_id, source_type, source_ref)` entry and the content hash skip make the second queue harmless.
    - **Bad payloads.** A webhook payload with no valid `test_case_id` is saved under the all zeros ID and indexed as "Test case 0000… failed". This is accepted as is.
    - **Tests.** A failed webhook payload and a failed batch item each queue one job. An unknown status (saved as `error`) queues one. A `passed` or `skipped` result queues none.
    - **Backfill.** Run the reindex script once to index earlier failed runs. `list_project_sources` already lists every failed run, whatever path saved it.

    Satisfies **AC-14**, **AC-15**.
13. [x] Add the `reindex_project_knowledge` script. Satisfies **AC-16**.

**Slice 4: the retrieval stage, feeding scenarios**
14. [x] Add the `retrieval` LLM role (settings, `LLM_ROLES`, and fallback to analysis) and the `RAG_*` loop settings, including `RAG_WORKFLOW_RETRIEVAL_ENABLED`. Satisfies **AC-22**.
15. [x] Set `project_id` in every `WorkflowState` creator: `run_workflow`, `WorkflowService`, the two state building routes in `main.py`, and `grounded_playwright_service`. Satisfies **AC-20a**.
16. [x] Build `KnowledgeRetrievalAgent` with its stage source map, query builders, the grade and rewrite prompts, the loop, the fallback, and the `WorkflowState` fields, including `retrieval_status`. Satisfies **AC-18**, **AC-19**, **AC-20**.
17. [x] Wire the graph: register the four retrieval nodes and add them to `routing_map`, add injection in `build_graph`, add the `_via_retrieval` helper to the supervisor routes, and turn the two fixed edges into conditional ones, with the flag read on every route call. Satisfies **AC-17**.
18. [x] Add the "Project knowledge" block to the `scenario_agent` prompt. Satisfies **AC-21**.

**Slice 5: the other consumers**
19. [x] Add the same block to `test_case_agent`, `requirement_analyst_agent`, `execution_analysis_agent`, and `defect_management_agent`. Satisfies **AC-21**.
20. [x] Move `FeatureInventoryAgent` to `KnowledgeService.search`, and delete `rag_service.py`. Satisfies **AC-23**.

**Slice 6: measure and trace**
21. [x] Add LangSmith spans around the search, grade, and rewrite calls. Satisfies **AC-24**.
22. [x] Build `RetrievalEvalService`, its fixture corpus and cases, its pytest test, and the `eval_retrieval` script. Satisfies **AC-25**.
23. [x] Write a migration for the `TestCase` groundedness fields, then add the groundedness judge to `evaluation_agent`. Satisfies **AC-26**.

**Docs** (each slice updates what it changes): `docs/api.md` (routes), `docs/architecture.md` and `docs/workflow.md` (the retrieval layer and stage), `docs/agents.md` (the new agent and fields), the chat tool catalog in spec 0005, README and `.env.example` (settings).

## Migration plan

**Strategy**: feature flagged, in place.

**Phases**:
1. Slices 1 and 2 ship together for anyone with `RAG_ENABLED=true`. Old documents are marked failed by the data migration, and users upload them again. The old global dataset is left untouched in RAGFlow (it is never read again), and an operator may delete it by hand.
2. Slice 3 ships. Run `reindex_project_knowledge --all` once to backfill existing test cases, stories, failures, defects, and locators.
3. Slices 4 and 5 ship with `RAG_WORKFLOW_RETRIEVAL_ENABLED=false`. Turn it on per environment after the slice 6 eval shows acceptable recall.

**Rollback**:
- Turning off `RAG_WORKFLOW_RETRIEVAL_ENABLED` restores today's graph.
- Turning off `RAG_ENABLED` stops all RAGFlow traffic.
- The schema changes only add nullable columns and one table, so earlier code keeps working against the new schema.

**Risks**:
- RAGFlow API differences between versions. The calls above are written against HTTP API v1; check them against the installed server in slice 1.
- Users must upload their documents again.

## Consequences

**Positive**:
- Project data is isolated by construction (one dataset per project), which closes a cross project read and delete hole.
- Uploaded documents are real for the first time.
- Every LLM backed agent can be grounded in project knowledge, and the grounding can be measured.
- The `Retriever` interface lets pgvector arrive later without touching agents or chat.

**Negative / tradeoffs**:
- Each workflow run that hits a retrieval stage costs up to 2 searches and 3 extra LLM calls, and adds latency, sometimes several seconds.
- One RAGFlow dataset per project means many datasets on the server to operate and back up.
- The in memory indexer queue loses pending upload and source retries on restart; the startup sweep and the reindex script are the recovery path. Remote deletes are persisted and retried (spec [0007](0007-persist-pending-ragflow-deletes.md)).
- Users must upload old documents again.
- Failure entries from UI and batch runs carry no failure analysis, because those paths never run the analysis agent. They are still searchable by test case title and error. The `analysis` retrieval stage (AC-17) also only runs for graph runs.
- The new `retrieval` role adds config surface.

**Neutral**:
- Four new graph nodes.
- A new permission module that existing roles get through the seeder.
- The settings payload changes `ragflow_api_key` to `ragflow_api_key_set`.

## Follow-up

- [ ] Check every RAGFlow endpoint and response shape in `RAGFlowRetriever` against the installed RAGFlow version during slice 1. The current `delete_document` uses a path form that may not match v1.
- [ ] Build a live labelled retrieval case file from a real project's documents, for the `eval_retrieval` script.
- [ ] Show `groundedness_score` and `unsupported_claims` on the test case detail view (this spec only exposes them through the API).
- [ ] Consider a pgvector `Retriever` implementation when running without RAGFlow is wanted.
- [ ] Consider a persistent indexing queue if restarts drop work often in practice. Deletes are already covered by spec [0007](0007-persist-pending-ragflow-deletes.md).
- [ ] Run failure analysis for UI and batch runs (a separate feature, decided 2026-11-18): after the webhook or batch saves a failed run, run the `analysis` retrieval stage and `ExecutionAnalysisAgent` in the background, save again, and queue the entry again so it gains the summary. It costs one LLM call per failed run.
- [ ] Verify the Jira stories and defects against a working Jira: moved to spec [0008](../0008-jira-knowledge-sources/index.md) on 2026-11-18, because the Jira site was unavailable.
