# 0006. Agentic RAG: rationale

## Context

The app already talks to RAGFlow (a hosted document parsing and search server), but in a way that makes retrieval both unsafe and nearly useless:

- **One shared dataset.** Every project's documents go into a single global `ragflow_dataset_id`, so a search in one project can return another project's content.
- **Unsafe delete.** Delete resolves documents by file name across that shared dataset, so deleting a file in one project can remove a same named file that belongs to another.
- **Uploads never send the file.** The Knowledge Base screen animates a progress bar and posts only metadata. The backend cannot find the file, so it writes two sentences of placeholder text and indexes that. Every "completed" document today is fake.
- **The routes are open.** The document routes check neither authentication nor project access.

Retrieval itself is shallow. Only `FeatureInventoryAgent` uses it: one query, top 3 chunks, with no check that they are relevant. The agents that would benefit most never see project knowledge: scenario and test case generation, requirement analysis, and execution failure analysis. The chat assistant (spec 0005) already runs a tool loop, but has no tool that searches documents.

The forces at play:

- **Project isolation.** It is an explicit project rule ("treat project data as project data, no global application fallback").
- **The existing stack.** RAGFlow is already integrated and configured. JSON is the default repository, and PostgreSQL is optional.
- **Test without live services.** Agents and runners must be testable with no live LLM or browser, so retrieval must be injectable.
- **Agent contract.** Agents follow the `WorkflowState` contract: validate, log, update only their own fields.
- **Cost.** Each LLM call counts against role pools and token budgets (specs 0003 and 0005).

Leaving this as it is means cross project leakage, and more work generated from content nobody uploaded.

## Options considered

### Option 1: Fix in place, one RAGFlow dataset per project, behind a Retriever interface

Keep RAGFlow. Create one dataset per project on first use and store its ID on the project. Put a small `Retriever` interface in front so chat, agents, and tests share one contract. Add a graded retrieval loop in a new agent.

**Pros**:
- Isolation is structural, because a project can only ever address its own dataset.
- Reuses the parsing (PDF, DOCX, XLSX), chunking, and embedding RAGFlow already provides. There is nothing new to operate.
- Small, incremental change that can be shipped slice by slice behind flags.

**Cons**:
- Many datasets on one RAGFlow server.
- Still depends on an external service being up.
- Source type filtering has to happen locally after the search.

### Option 2: Replace with an own vector store (pgvector)

Chunk and embed locally through LiteLLM, and store vectors in PostgreSQL with a `project_id` column.

**Pros**:
- No external service.
- A filter on `project_id` and `source_type` is exact and cheap.
- Everything lives in one database.

**Cons**:
- PostgreSQL is optional in this project (JSON is the default), so RAG would then require it.
- You rebuild document parsing, chunking, and embedding pipelines that RAGFlow already runs.
- Adds embedding cost and embedding everything again whenever the model changes.

### Option 3: Keep one shared dataset and tag chunks by project

Keep the single dataset, and add `project_id` metadata to each document, filtering on it at search time.

**Pros**:
- One dataset to operate.
- Smallest config change.

**Cons**:
- Isolation then depends on every caller remembering the filter. One missed filter leaks data.
- Metadata filtering support depends on the RAGFlow version.
- Deleting by name stays risky.

## Rationale

Option 1 is the only choice that makes isolation impossible to get wrong at the call site. Every retriever method takes a `Project`, and the only dataset it can reach is that project's own. That directly answers the cross project leak and the delete bug, which are the most serious problems in the Context. Option 3 keeps the same server but turns isolation into a convention that a single forgotten filter breaks. That is the kind of failure that ships silently and gets found by a customer.

Option 2 is cleaner on paper, but it forces PostgreSQL on a project whose default store is JSON, and it means rebuilding the document parsing RAGFlow already does well. The `Retriever` interface keeps that door open: when running without RAGFlow becomes a real need, pgvector arrives as a second implementation, and no agent or chat code changes.

Within Option 1, the smaller calls follow the same forces:

- **Datasets are created lazily**, so projects that never use knowledge never touch RAGFlow.
- **Search results are filtered locally**, because it works on any RAGFlow version and doubles as the mapping that labels each chunk with its source.
- **The grading loop is capped at 2 attempts**, which bounds cost at 2 searches and 3 LLM calls per stage.
- **The stage sits behind its own flag**, so chat search can ship before the workflow changes.
- **Retrieval never fails the workflow**, keeping the existing principle that RAG enrichment is optional.

The engineer chose a dedicated `retrieval` LLM role over reusing `analysis`. That works, and isolates grading load from evaluation load. The cost is one more role to configure, which the fallback to the analysis role keeps cheap by default.

### Decisions made in the design conversation

- RAGFlow per project; pgvector later behind the same interface.
- All four steps in one spec, sliced Tracer Bullet style; the real multipart upload is fixed.
- Upload accepts PDF, DOCX, TXT, MD, CSV, and XLSX up to 20 MB. The dataset is created lazily. Old documents are marked as needing upload again.
- All five consumer agents, scenario first.
- `Project.rag_dataset_id`, real `Document.remote_doc_id` and `error` columns, the new `KnowledgeEntry` entity, and `TestCase` groundedness fields.
- A new `retrieval` LLM role. A background queue thread for indexing. Loop limits: 2 attempts, top 8, need 2.
- A new `Knowledge` permission module. The old JSON route is removed. The only extra route is the status route.
- Local post filter for source types.
- Poll until parsed. Retry 3 times with backoff. Delete the dataset and files with the project. Chat search is open to anyone with project access.

### Calls made while writing the spec

- **Four retrieval nodes, one per stage**, over a single node with a dynamic router. The edges stay explicit, and no routing field is added to `WorkflowState`. Runner up: one node plus a `retrieval_target` field set by the supervisor.
- **`RAG_WORKFLOW_RETRIEVAL_ENABLED`, default false.** The graph change has the largest blast radius, so it gets its own switch. Runner up: reuse `RAG_ENABLED` alone.
- **Groundedness is reported, not enforced.** Scores need a baseline before they gate anything. Runner up: auto reject below a threshold.
- **The groundedness judge runs on the `analysis` role**, next to the rest of evaluation. Runner up: the `retrieval` role.
- **The settings payload returns `ragflow_api_key_set` instead of the key**, to close an existing secret exposure while the RAG settings change. Runner up: leave it as it is.
- **The retrieval role pool defaults are max concurrent 2, cooldown 30 s, max wait 20 s**, so a busy pool fails fast and retrieval is skipped rather than stalling the workflow. Runner up: the analysis role limits.

### Defect source (update, 2026-11-17)

**Context.** While building slice 3, `/develop` found that the original defect row could not be built. There is no local defect record. `DefectTrackerService` only reads from Jira, and has no create or update to hook. The bug title and description that `defect_management_agent` writes go to Jira and are not kept. Locally there is only the bug key, on `ExecutionResult.jira_bug_id` and `TestCase.jira_issue_key`. The sync bug path saves the key on the test case only. `JiraService.get_issue` returns `None` both for a missing issue and for a network error, and returns made up data in mock mode.

**Options considered.**
- **Read from Jira (chosen).** The ref is the bug key, a local link proves which project owns it, and the worker reads the current issue from Jira.
  - Pros: the bug's real title, status, and description are searchable, and they stay current (a webhook update or the next run reindexes them). No schema change, because `knowledge_entries` already holds it.
  - Cons: the indexer now depends on Jira being reachable, and each defect job costs one Jira call. Because `get_issue` cannot tell "deleted" from "unreachable", a bug deleted in Jira stays indexed until its local links go.
- **Fold into failures.** Drop `defect` as a source and add the bug key and URL to the `execution_failure` text.
  - Pros: simplest, with no Jira reads from the indexer and nothing new to fail.
  - Cons: the bug's own title and description, often the clearest statement of the product problem, are never searchable. The analysis stage would also lose `defect` from its source types.
- **Store a local copy.** Save the agent's bug title and description on the execution (two new nullable columns) and index that snapshot.
  - Pros: works offline and deterministically.
  - Cons: a snapshot that never reflects later Jira edits or status changes. Bugs linked as duplicates (the agent finds an existing issue) carry no text. It adds a migration to the executions table.

**Rationale.** The defect stage exists to ground execution analysis in what the team already knows about a failure, and that knowledge lives in the Jira bug, so the text has to come from there. Ownership comes from the local links, not from Jira, which keeps the key invariant (every chunk maps to a local record of the same project) without trusting Jira labels. Treating a failed read as transient, and letting only local links decide removal, means a Jira outage can never empty the index. Skipping mock mode keeps fake text out of a real dataset. Runner up: fold into failures, which is the fallback if Jira reads prove too slow or flaky in practice.

### Failed runs from every save path (update, 2026-11-18)

**Context.** `/check verify` ran a failing Adactin test case from the UI and found no `execution_failure` entry, and none anywhere in the database. The spec named only `run_execution_and_stream` as the hook. That is the Python graph path, and nothing in the current UI calls it. The UI Run button goes through the Next.js workspace, which posts the finished run to the execution webhook, and `ExecutionService.finalize_execution` saves it. The batch route saves its results in `ExecutionService.start_batch_run`. Neither path queued indexing, and neither runs the analysis agent, so their results have no `failure_category` or `root_cause_summary`.

**Options considered.**
- **Index without analysis (chosen).** Add the hook to both missing save paths. The entry holds what the run has: title and error, plus the analysis when one exists.
  - Pros: a few lines per path, no new LLM cost, and failures from the path people actually use become searchable at once. The reindex script backfills old runs.
  - Cons: UI and batch entries lack the root cause explanation, which is often the most useful text for the `analysis` stage.
- **Add analysis to UI and batch runs.** After the save, run the `analysis` retrieval stage and `ExecutionAnalysisAgent` in the background, save again, then index.
  - Pros: richer entries, and AC-17's analysis stage would ground UI runs too.
  - Cons: one more LLM call per failed run, a second save racing the report and Jira sync, and a change to run behaviour that deserves its own design and verification.

**Rationale.** The engineer chose to index without analysis. AC-15 asks hooks to sit where saves happen, and these are the real saves, so the gap was a missed call site, not a design flaw. Analysis for UI runs changes how every run behaves, which is outside this spec's job of indexing what exists. It is recorded as a follow up. Runner up: add analysis, the next step once the cost per failed run is accepted.
