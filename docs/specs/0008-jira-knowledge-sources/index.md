# 0008. Index Jira stories and Jira defects into the project knowledge base

**Date**: 2026-11-18
**Status**: In Progress

## Summary

This spec owns the two knowledge sources that are read from Jira: imported Jira stories and Jira bugs (defects) linked to a project. They were part of spec 0006 and are already built. They moved here so Agentic RAG could be verified while the Jira site was unavailable. What is left is to verify them against a working Jira; no new building is planned.

## Requirements

**User stories**:
- As a QA engineer, I want imported Jira stories in my project's knowledge base, so that chat and the workflow agents can use their acceptance criteria.
- As a QA engineer, I want the Jira bugs linked to my project's runs indexed with their current status, so that failure analysis can see known defects.

**Acceptance criteria** (moved from spec 0006: AC-1 is the `jira_story` part of 0006 AC-14, AC-2 is 0006 AC-14a, AC-3 is the Jira part of 0006 AC-16):
- **AC-1**: A Jira story imported through `POST /api/v1/projects/{project_id}/requirements/import-jira` is indexed as a `KnowledgeEntry` with `source_type` `jira_story`, `source_ref` = the issue key, and `title` = the story summary. Its text is read from the requirement made by the import: `Jira story <key>: <title>`, then the description, then the requirement's acceptance criteria (written by the requirement analyst after the import, not copied from Jira). The general entry rules of spec 0006 AC-14 apply: the same `content_hash` is not sent again, changed content replaces the old remote document, and deleting the requirement made from that story (the chat requirement delete) removes the entry and its remote document.
- **AC-2**: A defect's `source_ref` is its Jira bug key. It belongs to a project only while a live execution of that project has it as `jira_bug_id`, or a live test case of that project has it as `jira_issue_key`. When the job runs, the worker reads the issue from Jira and indexes its key, summary, status, priority, and description.
  - **Live.** A live execution is one the repository returns that is not soft deleted (`is_execution_deleted` is false; PostgreSQL deletes executions outright). A live test case is one `get_test_cases_for_project` returns, so the case, its scenario, and its requirement are not deleted.
  - **Key matching.** Keys are trimmed, then compared exactly (case sensitive). `source_ref` stores the trimmed key, and empty keys are ignored.
  - **No link left.** When no live execution or test case in the project links the key any more, the entry and its remote document are removed. The link check runs before any Jira read, so this also removes a `failed` entry. A closed or resolved bug stays indexed, with its new status.
  - **Jira unreachable.** A failed Jira read is retried on the indexer's retry delays, then the entry is marked `failed` with the error. A failed read never removes an entry.
  - **Jira not configured.** When `JiraService` is in mock mode (no Jira credentials), defects are skipped: no entry is written, and the mock issue text is never indexed.
- **AC-3**: `python -m backend.scripts.reindex_project_knowledge --project <id>` (or `--all`) also indexes every requirement with a `jira_issue_key` as a `jira_story` entry (read locally, no Jira call) and, with Jira configured, each distinct linked bug key once, through the same indexer, and counts them in its indexed, skipped, and failed totals.

## Decision

**Chosen option**: Option 1: a separate spec and scope feature for the Jira sources.

The Jira stories and defects keep the design they were built with under spec 0006, and their verification is tracked here, apart from Agentic RAG, until a working Jira is available.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

The design was decided and built under spec 0006. This spec owns those parts; spec 0006 marks them as governed here. Read them in place:
- [0006 Feature design > Indexing hooks](../0006-agentic-rag/index.md), the `jira_story` and `defect` rows: when each is queued and removed.
- [0006 Feature design > Defect source](../0006-agentic-rag/index.md): the reader, mock mode guard, `SourceUnavailable`, field shapes, text format, and ordering.

**Data model sketch**: no new tables or columns. Both sources are `KnowledgeEntry` rows (spec 0006 data model), keyed unique on `(project_id, source_type, source_ref)`. The local Jira links already exist: `Requirement.jira_issue_key`, `ExecutionResult.jira_bug_id`, and `TestCase.jira_issue_key`.

**State transitions**: a `KnowledgeEntry` moves `pending → indexed | failed`, as in spec 0006. A defect entry in `failed` is retried on the next job or reindex; it is removed only when no local link is left.

**API surface** (all existing, no change):
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/v1/projects/{project_id}/requirements/import-jira` | POST | `issue_key`: str (req), `release_id`: UUID (opt) | the requirement; queues a `jira_story` job | bearer | 404 project or issue not found |
| `/api/v1/projects/{project_id}/executions/{execution_id}/sync-bug` | POST | path IDs | sync result; queues `defect` jobs for the keys read back | bearer | 404 |
| `/api/v1/webhooks/jira` | POST | Jira webhook body (`issue.key`, `issue.fields.status.name`) | `ignored` or processed; queues a `defect` job for every project still linking the key, before the resolved status filter | none (no secret is checked) | none; a body without a key or status is `ignored` |

**Value sourcing**:
| Action | Value produced or displayed | Source |
|---|---|---|
| story entry | `source_ref`, `title` | the issue key from the import call; the Jira `summary` saved on the requirement |
| story entry | text | the requirement found by `jira_issue_key`: its title (the Jira summary), description (from Jira at import), and acceptance criteria (from the requirement analyst) |
| defect entry | belongs to the project | a live `ExecutionResult.jira_bug_id` or live `TestCase.jira_issue_key` of this project equal to the trimmed key |
| defect entry | text and `title` | `JiraService.get_issue(key)` read when the job runs; title is the summary, or the key when empty |
| defect entry | status after a Jira change | the webhook queues a job, and the worker reads the issue again |
| reindex script | story and defect refs | `get_requirements` (Jira linked) and the deduplicated trimmed bug keys of live executions and test cases |

**Key invariants**:
- A defect is indexed for a project only while a live local link exists; a Jira read never decides removal.
- Mock mode text is never indexed.
- The same issue always produces the same text, so an unchanged issue is never uploaded twice.
- A defect search in one project never returns another project's defect (each project searches only its own dataset, spec 0006).

**Security model**: no new access rules. Entries are read through `search_knowledge` and the retrieval stage, which need project access (spec 0006 AC-6 and AC-12). Jira credentials stay in `.env` and are never returned by a route.

**Configuration required** (all exist):
- `JIRA__BASE_URL`, `JIRA__EMAIL`, `JIRA__API_TOKEN`, `JIRA__PROJECT_KEY`: the Jira site. Without them `JiraService` runs in mock mode and AC-2 skips defects.
- A Jira webhook pointed at `/api/v1/webhooks/jira`, needed only for the status change check in AC-2. No secret is configured or checked.
- Prerequisite: a reachable Jira Cloud site, for the story import and every defect step. The story delete and the story half of the reindex read local data only. On 2026-11-18 the configured site answered "Site temporarily unavailable" to every call.

**Critical test scenarios** (already in `backend/tests/test_knowledge_defects.py` and `test_knowledge_base.py`, with a fake `JiraService`):
- Happy path: a bug key on a live execution of project A is indexed with its Jira summary and status, verifies **AC-2**.
- Isolation: searching in project B never returns A's defect, verifies **AC-2**.
- Failure case: a Jira read that fails is retried, then the entry is `failed`, and an existing entry is never removed by it, verifies **AC-2**.
- Removal: deleting the last link removes the entry; deleting one of two keeps it, verifies **AC-2**.
- Story: importing a story indexes a `jira_story` entry, and the chat requirement delete removes it, verifies **AC-1**.
- Reindex: with Jira configured each linked key is indexed once; in mock mode defects count as skipped, verifies **AC-3**.

## Build plan

All tasks were built under spec 0006 (tasks 12 and 12a). Nothing new to build; the remaining box is verification.

1. [x] Story hook on the Jira import route and removal on the chat requirement delete (0006 task 12), satisfies **AC-1**
2. [x] Defect reader with injected `JiraService`, mock mode guard, live link check, field shapes, and the four enqueue hooks plus delete key capture (0006 task 12a), satisfies **AC-2**
3. [x] Reindex script story and defect keys (0006 task 13), satisfies **AC-3**
4. [ ] Verify against a working Jira: the steps in [verify.md](verify.md), satisfies **AC-1**, **AC-2**, **AC-3**

## Consequences

**Positive**:
- Agentic RAG (spec 0006) can be verified and closed without Jira.
- The Jira dependent checks stay recorded and owned, not dropped.

**Negative / tradeoffs**:
- The Jira code is in production paths before its live behavior is verified; only fake Jira tests cover it until a working Jira is available.
- The design text stays in spec 0006, so a reader of this spec must follow links into 0006.

**Neutral**:
- No code, schema, or config change.

## Follow-up

- [ ] Restore access to the Jira site (or point `.env` at another Jira Cloud site), then run `/check verify jira knowledge sources`.
- [ ] `import_jira_story` returns 404 both for a missing issue and for a Jira network error (`get_issue` returns `None` for both). Consider a 502 for the network case so the user knows to retry.
- [ ] `POST /api/v1/webhooks/jira` checks no secret, so anyone who can reach it can queue defect reindexes and run the resolved status flow. Consider a shared secret (a new `JIRA__WEBHOOK_SECRET`) checked on every call. This predates the split; it needs its own decision.
