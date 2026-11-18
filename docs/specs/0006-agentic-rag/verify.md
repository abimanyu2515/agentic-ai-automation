# Verify: Agentic RAG · spec 0006 · updated 2026-11-17
_Steps derived from spec 0006 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

Slice 1 (one real file end to end into chat) plus build plan task 6 (the `Knowledge` permission). Later slices append their steps here.

Setup you may want: `RAG_ENABLED=true`, `RAG_VECTOR_DB_PROVIDER=ragflow`, `RAGFLOW_API_BASE`, and `RAGFLOW_API_KEY` pointing at a running RAGFlow, then restart the API so the seeder adds the `Knowledge` grants.

## UI / manual
- [x] Select project A, open **Knowledge Base** in the sidebar → the screen shows the drop zone, a "Choose a file" button, and the document list → AC-11
- [x] Choose a small `.txt` file with a distinctive sentence → status reads "Uploading …" until the 201 arrives, then the row shows **Pending** or **Indexing…** → AC-1, AC-11
- [x] Stay on the screen → the row updates by itself every 5 s and ends **Indexed**; the browser network tab shows the list polling stop once nothing is pending or processing → AC-3, AC-11
- [x] Leave the screen while a row is still indexing → list polling stops (no more `GET …/documents` calls) → AC-11
- [x] In RAGFlow, a dataset named `proj-<project A id>` exists, holds the file, and its ID equals `projects.rag_dataset_id`; no dataset outside the `proj-` naming is created → AC-2
- [x] Upload a second file to project A → no second dataset is created → AC-2
- [x] Upload a `.exe` → "Upload failed: File type not allowed…" (415); an empty `.txt` → 422; a file over 20 MB → 413 → AC-1
- [x] Upload a `.md` file → accepted even though browsers send it as `application/octet-stream` or blank → AC-1
- [x] Upload a file named `CON.txt` → the row shows `CON.txt` as the original name; the saved file under `data/projects/<project>/documents/<doc>/` is `_CON.txt` → AC-1
- [x] Upload the same file name twice → two rows, two separate `<document_id>` folders → AC-1
- [x] Make RAGFlow unreachable (wrong `RAGFLOW_API_BASE`), upload → the row ends **Failed** with the error text under the badge → AC-3, AC-11
- [x] In chat on project A, ask "what does our spec say about <the distinctive sentence>?" → the assistant calls `search_knowledge` (panel shows "Looking up the project knowledge base…") and names the file title it used → AC-12, AC-13
- [x] In chat on project B (no uploads), ask the same → the assistant says the knowledge base is not set up for this project → AC-12
- [x] Upload `spec.txt` to both A and B with different text; in chat on B, search for A's text → only B's passage comes back → AC-12, invariant "a chunk maps to a local record of the same project"
- [x] Sign in as a Stakeholder → Knowledge Base is visible, but uploading returns 403 → AC-6
- [x] Sign in as a user not on project A → `GET /api/v1/projects/<A>/documents` returns 403 → AC-6

## Commands
- [x] `python -m pytest -q backend/tests/test_knowledge_base.py` → all pass → AC-1, AC-2, AC-3, AC-6, AC-8, AC-12
- [x] `python -m alembic current` → `f2b6c8d41a07 (head)` → data model
- [x] `curl -X POST -H "Authorization: Bearer <token>" -F "file=@notes.md" http://localhost:8000/api/v1/projects/<id>/documents/upload` → 201 with `embedding_status: "pending"` → AC-1
- [x] `curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d "{}" http://localhost:8000/api/v1/projects/<id>/documents` → 405 (old JSON route removed) → AC-6
- [x] `curl` without a token to `…/documents` → 401 → AC-6
- [x] `DELETE …/projects/<B>/documents/<a document of A>` → 404, and A's document is still listed → AC-5 (scoping part)

## Value sourcing checks
- [x] `storage_path` → equals `<RAG_STORAGE_ROOT>/<project_id>/documents/<document_id>/<sanitized name>`; change `RAG_STORAGE_ROOT`, restart, upload → the new root is used
- [x] `mime_type`, `size` → `size` matches the file's byte count; `mime_type` is what the browser sent (or a guess from the name)
- [x] allowed type → change `RAG_UPLOAD_TYPES=txt`, restart → a `.pdf` now gets 415
- [x] dataset to use → `projects.rag_dataset_id` is set after the first index and never changes, even after you edit the project's settings
- [x] `remote_doc_id` → matches the document's ID in RAGFlow's dataset view
- [x] completed or failed → a document RAGFlow fails to parse ends `failed` with RAGFlow's message; set `RAG_PARSE_TIMEOUT_SECONDS=10` on a large PDF → "parsing timed out"
- [x] search `source_type`, `source_ref`, `title` → a chat result for a document shows `document`, the document ID, and the original file name
- [x] search `score` → equals RAGFlow's chunk `similarity`

## Acceptance-criteria coverage
- AC-1: upload, type, size, empty, names, duplicates steps · AC-2: dataset steps · AC-3: indexed and failed steps · AC-5: cross project delete (scoping only; remote delete is slice 2) · AC-6: Stakeholder, outsider, no token, old route · AC-8: covered by `test_disabled_rag_saves_the_file_and_waits` · AC-11: screen, uploading, polling steps · AC-12: chat search and isolation steps · AC-13: chat names its sources
- Not yet built (later slices): AC-4, AC-5 remote delete, AC-7, AC-9, AC-10, AC-14 to AC-26

---

# Verify: Agentic RAG slice 2 · spec 0006 · updated 2026-11-17
_Build plan tasks 7 to 10: safe delete, retries and startup sweep, status route, old document migration, project cleanup, and the write only key._

## UI / manual
- [x] Upload a file to project A and wait for **Indexed**; delete it → the row disappears, `data/projects/<A>/documents/<doc>/` is gone, and a few seconds later the document is gone from `proj-<A>` in RAGFlow → AC-5
- [x] Upload `spec.txt` to A and B; delete B's → A's row, file, and RAGFlow document are untouched → AC-5
- [x] Upload a large PDF and delete it while it still shows **Indexing…** → no copy of it is left in `proj-<project>` in RAGFlow once the indexer finishes → AC-5
- [x] Stop RAGFlow, upload a file, then start RAGFlow again within about a minute → the row stays **Indexing…** and then ends **Indexed** (retried after 10 s, then 60 s) → AC-4
- [x] Keep RAGFlow stopped for more than 6 minutes after an upload → the row ends **Failed** with "RAGFlow unreachable: …" → AC-4
- [x] Upload a large file, restart the API while the row is **Indexing…** → after the restart the row still reaches **Indexed**, and the server log shows "Knowledge sweep queued N document(s)" → AC-4
- [x] Set `RAG_ENABLED=false`, restart, upload → the row shows **Pending** with "knowledge base is disabled"; set it back to true and restart → the row reaches **Indexed** → AC-8
- [x] Open **Settings** → the RAGFlow Dataset ID field is gone, and the API key field is empty with a "Key is set" placeholder when `RAGFLOW_API_KEY` is set → AC-2, key invariant
- [x] Save settings without typing a key → RAGFlow keeps working, and `backend/config/override.yaml` has no `ragflow_api_key` or `ragflow_dataset_id` → key invariant
- [x] Delete a project that has uploads → `data/projects/<project id>/` is gone right away, and `proj-<project id>` disappears from RAGFlow shortly after → AC-10
- [x] Delete a project while RAGFlow is stopped → the delete still succeeds, and the server log shows "Knowledge dataset delete failed" → AC-10

## Commands
- [x] `python -m pytest -q backend/tests/test_knowledge_base.py` → all pass → AC-4, AC-5, AC-7, AC-8, AC-9, AC-10, key invariant
- [x] `python -m alembic current` → `a3d7f1c9e526 (head)` → AC-9
- [x] `curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/projects/<id>/knowledge/status` → 200 with `enabled`, `dataset_id`, `documents` counts, `entries`, and `last_indexed_at` → AC-7
- [x] The same call as a user not on the project → 403; without a token → 401 → AC-6, AC-7
- [x] `curl http://localhost:8000/api/v1/settings` → `rag` has `ragflow_api_key_set`, and no `ragflow_api_key` or `ragflow_dataset_id` → key invariant
- [x] JSON store: add a document whose `storage_path` does not contain its own ID to `data/store.json`, remove the `knowledge_migration_0006` key, restart → that document is `failed` with "file was never uploaded, please upload it again", and the marker is back; restart again → nothing else changes → AC-9

## Value sourcing checks
- [x] dataset to delete (project delete) → the dataset removed from RAGFlow is the one in `projects.rag_dataset_id` before the delete, not any other project's
- [x] `last_indexed_at` → equals the newest `uploaded_at` among **completed** documents; a newer pending upload does not change it
- [x] retry timing → set `RAG_INDEX_RETRY_DELAYS=5,5`, restart, stop RAGFlow, upload → the log shows two retries about 5 s apart, then the row is **Failed**

## Acceptance-criteria coverage
- AC-2: settings form and payload no longer carry a global dataset · AC-4: retry, give up, restart steps · AC-5: delete, cross project, delete during upload · AC-6: status and delete outsider checks · AC-7: status call and `last_indexed_at` check · AC-8: disabled mode step · AC-9: migration revision and JSON marker step · AC-10: project delete steps
- `entries` in the status stays `{}` until slice 3 indexes knowledge entries (AC-7 completes then)

## Slice 3: index the rest of the project (AC-14 to AC-16) · added 2026-11-17

Jira stories and defects are verified under [spec 0008](../0008-jira-knowledge-sources/verify.md).

### UI / manual
- [x] With `RAG_ENABLED=true`, approve a test case on the Test Cases screen → within a few seconds `GET /knowledge/status` shows `entries.test_case.indexed` up by 1, and chat `search_knowledge` for words in its steps returns it with `source_type: test_case` and its title → AC-14, AC-15, AC-7
- [x] Reject that test case → its entry is removed and search no longer returns it → AC-14
- [x] Approve it again, then edit only its expected result → the entry keeps its ID, `remote_doc_id` changes, and the old RAGFlow document is gone from the project's dataset → AC-14
- [x] Save the same test case again with no change → the log shows "Knowledge entry unchanged", and nothing is uploaded → AC-14
- [x] Delete the test case → its entry and RAGFlow document are removed → AC-14
- [x] Run a test case that fails → an `execution_failure` entry titled "<test case> failed" appears, and searching its error text returns it; a passing run adds nothing → AC-14, AC-15
- [x] Delete that execution (or `DELETE /executions/failed`) → its entry is removed → AC-14
- [x] Add, edit, or delete an element on an object repository page → the page's `locator` entry is indexed again with the new locators; delete the page → its entry is removed → AC-14, AC-15
- [x] Stop RAGFlow, then edit an approved test case → the edit saves at normal speed and returns 200; after the retries the entry shows `failed` with the error → AC-15, AC-4
- [x] Project B: search for project A's test case title → nothing from A comes back → AC-12, key invariant

### Commands
- [x] `python -m backend.scripts.reindex_project_knowledge --project <id>` → prints the queued count, then "Indexed N, skipped (unchanged) 0, failed 0, removed M" → AC-16
- [x] Run it again with no changes → "Indexed 0, skipped (unchanged) N" → AC-16, AC-14
- [x] Add a `knowledge_entries` row whose source does not exist, then run it → "removed 1" → AC-16
- [x] With `RAG_ENABLED=false` → it exits with code 1 and says the knowledge base is disabled → AC-16, AC-8
- [x] PostgreSQL: `alembic current` → `b8c4e2f17d93`, and `\d knowledge_entries` shows the unique `(project_id, source_type, source_ref)` and the `(project_id, remote_doc_id)` index → AC-14

### Value sourcing checks
- [x] `content_hash` → equals the sha256 of the exact `.txt` uploaded to RAGFlow (named `<source_type>__<source_ref>.txt`)
- [x] source text → a test case holds its title, steps, and expected result; a failure holds its test case title, error, and analysis summary; a page holds its name and each element's locators
- [x] current source content → edit a test case twice quickly → the indexed text matches the second edit, not the first
- [x] search `source_type`, `source_ref`, `title` → come from the `knowledge_entries` row matched by `remote_doc_id`, never from the chunk text
- [x] `last_indexed_at` → now also moves forward when a knowledge entry is indexed

### Acceptance-criteria coverage (slice 3)
- AC-14: approve, reject, edit, unchanged, delete, failure, locator steps (Jira stories and defects: spec 0008) · AC-15: timing and RAGFlow down steps · AC-16: reindex command steps · AC-7: `entries` counts now filled

## Slice 3: defects read from Jira · moved 2026-11-18

These steps, and the Jira story steps, moved to [spec 0008 verify.md](../0008-jira-knowledge-sources/verify.md) so this spec can be verified without a working Jira.

## Slices 4 and 5: the retrieval stage feeding all five agents (AC-17 to AC-23, AC-20a) · added 2026-11-17

_You may run these with `RAG_ENABLED=true`, a RAGFlow server, and a project whose knowledge base already holds a completed document. Each step names the criterion it checks._

### UI / manual
- [x] Keep `RAG_WORKFLOW_RETRIEVAL_ENABLED=false`, generate scenarios for a requirement → the run log shows no `Knowledge Retrieval Agent` lines and scenarios generate as before → AC-17
- [x] Set `RAG_WORKFLOW_RETRIEVAL_ENABLED=true`, restart, and generate scenarios for a requirement whose topic is in an uploaded document → the log shows `Knowledge Retrieval Agent started (stage scenarios)` before `Scenario Agent started`, then `finished ... ok, N chunks` → AC-17, AC-19
- [x] With the flag on, generate test cases from approved scenarios → the log shows stage `test_cases` before the Test Case Agent → AC-17, AC-18
- [x] With the flag on, ingest a new requirement → stage `requirements` runs before the Requirement Analyst Agent → AC-17
- [x] With the flag on, run a test case that fails → stage `analysis` runs after execution and before the analysis; the defect agent runs without its own retrieval stage → AC-17, AC-21
- [x] With the flag on, generate scenarios in a project with no uploads (no dataset) → the run finishes, and the log shows `skipped (stage scenarios): the project has no knowledge dataset yet` → AC-20
- [x] Stop RAGFlow, then generate scenarios with the flag on → the run still finishes, and the stage logs `skipped ... knowledge search failed` → AC-20
- [x] Open Master Settings → the LLM roles list shows a Retrieval role, with no API key shown → AC-22
- [x] Open Token Utilization after a run with the flag on → grade and rewrite calls appear under agent `knowledge_retrieval_agent` → AC-22
- [x] With RAG on and a document indexed, ingest a requirement on that topic → its `feature_mapping` holds text from this project's documents only → AC-23

### Commands
- [x] `pytest -q backend/tests/test_knowledge_retrieval.py` → all pass (loop, skips, queries, graph order with the flag on and off, prompt block, retrieval role, feature inventory) → AC-17 to AC-23
- [x] `pytest -q backend/tests/test_graph.py backend/tests/test_llm_roles.py` → all pass; the default flag off graph keeps today's order → AC-17, AC-22
- [x] `grep -rn "rag_service\|RAGFlowService" backend --include=*.py` → no import or use left (only a historical mention in `retrieval/factory.py`'s docstring) → AC-23

### Value sourcing checks
- [x] The project: run with a state that has no `project_id` (for example `run_workflow(req)` without one) → stage `skipped`; with `project_id` set, the stage searches only that project's dataset (search from project B never returns project A's chunk) → AC-20, AC-20a
- [x] Stage: the `analysis` node searches `execution_failure, defect, test_case, locator` and the `scenarios` node searches `document, jira_story` (`retrieval_sources` on the final state) → AC-18
- [x] Query: a requirement with acceptance criteria → `retrieval_query` holds title, description, and criteria; for analysis with no failed runs → stage skips with "the query is empty" → AC-18
- [x] Relevant flag: make the grader answer with something that is not JSON (a stub) → the top `RAG_MIN_RELEVANT_CHUNKS` chunks are kept with `graded: false` and the log says "grading failed" → AC-20

### Acceptance-criteria coverage (slices 4 and 5)
- AC-17 by the flag off and flag on runs and the graph tests · AC-18 by the stage and query checks · AC-19 by the scenarios run and the loop tests · AC-20 by the no dataset, RAGFlow down, and grader fallback checks · AC-20a by the project check · AC-21 by the prompt block tests and the defect run · AC-22 by Master Settings and Token Utilization · AC-23 by the feature mapping run and the grep

---

# Slice 6: measure and trace · updated 2026-11-17

Setup you may want: `RAG_ENABLED=true`, `RAG_WORKFLOW_RETRIEVAL_ENABLED=true`, a project with indexed documents, and for tracing `LANGSMITH_TRACING=true` plus `LANGSMITH_API_KEY`. Restart the API after changing `.env`.

## UI / manual
- [x] With tracing on, generate test cases for a requirement the documents describe → in LangSmith, under the `retrieval_test_cases` node, you see `knowledge_retrieval.search` and `knowledge_retrieval.grade` spans (plus `.rewrite` and a second search when fewer than 2 chunks were relevant) → AC-24
- [x] Open one of those spans → metadata shows `stage: test_cases`, `attempt`, `query`, `source_types: [document, jira_story, test_case, locator]`, `returned_source_refs`, and `relevant_count`; span inputs hold no chunk text or project object → AC-24
- [x] Set `LANGSMITH_TRACING=false`, restart, generate again → the run completes the same way and nothing new appears in LangSmith → AC-24
- [x] After that generation, `GET /api/v1/projects/<id>/testcases` (or the `generate-testcases` response itself) → each test case has a `groundedness_score` between 0 and 1 and an `unsupported_claims` list → AC-26
- [x] Vary the source: generate for a requirement whose documents say nothing about it, or with `RAG_WORKFLOW_RETRIEVAL_ENABLED=false` → `groundedness_score` is `null`, `unsupported_claims` is `[]`, and evaluation status is still `needs_review` or `rejected` exactly as before → AC-26, value sourcing row "groundedness"
- [x] Point the analysis role at a bad model name, generate with retrieval on → the evaluation step still completes (if the main relevance call succeeds), groundedness stays `null`, and the server log shows "groundedness scoring skipped" → AC-26

## Commands
- [x] `python -m pytest -q backend/tests/test_retrieval_eval.py backend/tests/test_evaluation_agent.py backend/tests/test_knowledge_retrieval.py` → all pass → AC-24, AC-25, AC-26
- [x] `python -m alembic current` → `c6f0a2d84e19 (head)`, and `test_cases` has `groundedness_score` (float, nullable) and `unsupported_claims` (JSON, default `[]`) → data model
- [x] Write a cases file with 3 to 5 real questions about a project's uploaded documents, using `document:<document_id>` refs from `GET /api/v1/projects/<id>/documents`, then `python -m backend.scripts.eval_retrieval --project <id> --cases cases.json --k 5` → per case recall@5 and reciprocal rank, then overall recall@5 and MRR → AC-25
- [x] Same command with `--json` → a JSON report with `k`, `case_count`, `recall_at_k`, `mrr`, and `cases` → AC-25
- [x] Same command with an unknown project ID, or with `RAG_ENABLED=false` → prints `error: …` and exits with code 2 → AC-25
- [x] Same command against a second project that has none of those documents → recall and MRR are 0 (no cross project results) → AC-25, isolation invariant

## Acceptance criteria coverage (slice 6)
- AC-24: the three LangSmith steps and `test_knowledge_retrieval.py` span tests
- AC-25: the four `eval_retrieval` steps and `test_retrieval_eval.py`
- AC-26: the three groundedness steps, the migration step, and the groundedness tests in `test_evaluation_agent.py`

# Verify: review fixes · spec 0006 · updated 2026-11-18
_Steps for the fixes from review `docs/reviews/2026-11-18-main.md`. `/check verify` runs these; `test_route_auth.py` locks the durable ones._

## UI / manual
- [x] Sign in as a Stakeholder assigned to a project, then call PUT and DELETE on one of its test cases, DELETE one of its executions, DELETE `executions/failed`, and DELETE the project → every call returns 403 and nothing changes → AC-6, AC-10 (review major 1)
- [x] Sign in as a Project Manager who is not on the project and send the same test case and execution calls → 403 → review major 1
- [x] As a Project Manager on project A, call A's test case and execution routes with an ID from project B → 404, and B's rows are unchanged → isolation invariant
- [x] Sign in as an Admin and save Master Settings (for example change `ragflow_api_base`) → 403, and `backend/config/override.yaml` is unchanged. As Superadmin the same save works → review major 1
- [x] Generate test cases with `RAG_WORKFLOW_RETRIEVAL_ENABLED=true` on a requirement whose project has indexed documents → `groundedness_score` is a number, not `null`, and the log shows "groundedness scored" → AC-26

## Commands
- [x] `python -m pytest -q backend/tests/test_route_auth.py` → 6 pass, including the JSON two thread write test → review majors 1 and 2

# Verify: Persist pending RAGFlow deletes · spec 0007 (child of 0006) · updated 2026-11-18
_Steps derived from spec 0007 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones. "0007 AC-N" means spec 0007's criteria, not 0006's._

## UI / manual
- [x] With RAGFlow running, upload a file to a project, wait for `completed`, then delete it → `GET /knowledge/status` shows `pending_deletes.count` 0 within seconds, and the document is gone from the RAGFlow dataset → 0007 AC-1, AC-4
- [x] Stop RAGFlow, delete an indexed document → the delete returns 200 right away; after the short retries (10, 60, 300 s) the status shows `count: 1` and a `last_error` such as "RAGFlow unreachable" → 0007 AC-3 (local delete never fails), AC-5, AC-11
- [x] With that row pending, restart the server, then start RAGFlow → the startup log says "Knowledge sweep queued N document(s) and 1 pending delete(s)", and the row disappears → 0007 AC-6
- [x] Stop RAGFlow, delete a whole project that has documents → one `dataset` row with reason `project_deleted`, and no `document` rows for that dataset (absorbed). Start RAGFlow and wait up to `RAG_DELETE_RESWEEP_MINUTES` (set it to 1 to test) → the dataset is gone from RAGFlow and the row is removed → 0007 AC-2, AC-7
- [x] Set `RAG_ENABLED=false` with a pending row and restart → the row stays and `attempts` does not change; enable again and restart → the row clears → 0007 AC-8
- [x] Make a document fail parsing (an unsupported or corrupt file, or set `RAG_PARSE_TIMEOUT_SECONDS=1`) → the document is `failed` with no `remote_doc_id`, and its remote copy is deleted. Upload it again → the RAGFlow dataset holds exactly one copy → 0007 AC-9
- [x] Sign in as a Stakeholder with `Knowledge:view` → the status route returns `pending_deletes`; a user with no access to the project gets 403 → 0007 AC-11

## Commands
- [x] `alembic current` → `a9d3f6b21c58 (head)`; table `knowledge_remote_deletes` exists with the partial unique indexes `uq_knowledge_remote_deletes_document` (`kind = 'document'`) and `uq_knowledge_remote_deletes_dataset` (`kind = 'dataset'`), plus `ix_knowledge_remote_deletes_project_id` → 0007 AC-2
- [x] `python -m pytest -q backend/tests/test_knowledge_remote_deletes.py backend/tests/test_ragflow_retriever.py` → all pass, and the two PostgreSQL tests are not skipped → 0007 AC-1 to AC-13
- [x] `python -m backend.scripts.reindex_project_knowledge --pending-deletes` on the JSON store with one row → prints the row line (project, kind, dataset, remote, reason, attempts, age, last_error), then "Listed only…", then `deleted 0, still pending 1`, exit code 1 → 0007 AC-12
- [x] Same with `--run` (server stopped) and RAGFlow up → `deleted 1, still pending 0`, exit code 0 → 0007 AC-12
- [x] Against the live RAGFlow, delete a dataset ID that does not exist → `RemoteNotFound` (the installed version answers code 403 "Permission denied"); delete an unknown document in a real dataset → `RemoteNotFound` after the lookup (code 102) → 0007 AC-13

## Value sourcing checks
- [x] Delete a project, then edit nothing and look at the row → `dataset_id` is the project's old `rag_dataset_id`, captured before the delete, never read again from settings or a route → value: enqueue `dataset_id`
- [x] A row from a deleted project still runs: its target is built from the row's own `project_id` and `dataset_id` → value: worker call target
- [x] `last_attempt_at` is wall clock UTC (it matches the log time), not a monotonic number → value: `last_attempt_at`
- [x] With two failed rows, `last_error` on the status is the one with the later `last_attempt_at` → value: status `last_error`

## Acceptance-criteria coverage
- 0007 AC-1 by the first manual step and the restart, order, and upload race tests · AC-2 by the project delete step and the migration check · AC-3 by the outage step · AC-4 by the first step · AC-5 by the outage step · AC-6 by the restart step · AC-7 by the project delete step · AC-8 by the disabled step · AC-9 by the parse failure step · AC-10 by `test_a_losing_dataset_writer_deletes_its_extra_dataset` (no manual step: it needs a race) · AC-11 by the outage and Stakeholder steps · AC-12 by the two script commands · AC-13 by the live RAGFlow command

# Verify: Index failed runs from every save path · spec 0006 task 12b · updated 2026-11-18
_Steps derived from spec 0006 AC-14 and AC-15, task 12b. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [x] With `RAG_ENABLED=true`, click Run on a test case that fails (UI run through the workspace webhook) → within a few seconds `GET /knowledge/status` shows `entries.execution_failure.indexed` up by 1, and chat `search_knowledge` for its error text returns an entry titled "<test case> failed" → AC-14, AC-15
- [x] Run a batch with one failing and one passing test case → exactly one new `execution_failure` entry, for the failed item → AC-14
- [x] Run a passing test case from the UI → no new `execution_failure` entry → AC-14
- [x] Post the same failed webhook payload twice (same `execution_id`) → still one entry, and the log shows "Knowledge entry unchanged" the second time → AC-14
- [x] Stop RAGFlow, then run a failing test case from the UI → the result saves and the run completes at normal speed; the entry ends `failed` after the retries → AC-15
- [x] Delete that execution → its entry is removed → AC-14

## Commands
- [x] `python -m pytest -q backend/tests/test_execution_service.py` → all pass (failed, error, unknown status queue one job; passed and skipped queue none; a failed batch item keeps its job even when a later report step raises) → AC-14, AC-15
- [x] `python -m backend.scripts.reindex_project_knowledge --all` → earlier failed runs from every save path are indexed (run on 2026-11-18: 83 `execution_failure` entries indexed, none failed; the 162 failures were `defect` entries, from Jira being down, tracked in spec 0008) → AC-14, AC-16

## Value sourcing checks
- [x] A UI run with an `error_message` → the entry text holds the test case title and that error, and no failure category or summary (UI and batch runs are not analysed) → value: entry source text
- [x] A webhook payload with an unknown status such as `timed_out` → saved as `error` and indexed → value: status rule (`FAILED_EXECUTION_STATUSES`)

## Acceptance-criteria coverage
- AC-14 by the UI, batch, passing, re-post, delete, and backfill steps · AC-15 by the RAGFlow down step and the unit tests (hook runs after the save, before later steps that can raise)
