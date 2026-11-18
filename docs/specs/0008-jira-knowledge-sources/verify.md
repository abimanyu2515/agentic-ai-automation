# Verify: Jira knowledge sources · spec 0008 · updated 2026-11-18
_Steps moved from spec 0006 `verify.md` (its slice 3 Jira steps). `/check verify` runs these; `/test` locks the durable ones. Ticks carried over were already passed under 0006._

You need RAG enabled and the indexer running. The story import and every defect step need a real Jira (credentials in `.env`, so `JiraService` is not in mock mode). The story delete, the story reindex, and the story text check read local data only: you can run them now on a requirement saved with a `jira_issue_key`. For the status change step, a Jira webhook pointed at `/api/v1/webhooks/jira` (no secret).

## UI / manual
- [ ] Import a Jira story (`POST /requirements/import-jira`), then read the acceptance criteria the analyst wrote on the new requirement → a `jira_story` entry keyed by the issue key appears, and searching one of those criteria returns it → AC-1 (needs Jira)
- [ ] Delete the requirement made from that story in chat → its `jira_story` entry and remote document are removed → AC-1 (local)
- [ ] Run a test case that fails and gets a bug from the defect agent (or `POST /executions/{id}/sync-bug`) → `GET /knowledge/status` shows `entries.defect.indexed` = 1 → AC-2
- [ ] In chat, ask about the bug's summary → `search_knowledge` returns an item with `source_type: defect`, `source_ref` = the bug key, title = the Jira summary → AC-2
- [ ] Move the bug from Open to In Progress in Jira (webhook configured) → the entry is reindexed with the new status, even though In Progress is not a resolved status → AC-2
- [ ] Close the bug in Jira → the entry stays `indexed` with status Closed → AC-2
- [ ] In a second project, search for the same summary → no defect item comes back → AC-2
- [ ] Delete one of two executions that link the key → the entry stays; delete the last link (execution and test case) → the entry and its RAGFlow document are gone → AC-2
- [ ] With a defect linked only through a test case's `jira_issue_key`, delete that test case's scenario (or its requirement) → the test case no longer counts as live, and the entry and its RAGFlow document are removed → AC-2
- [ ] Click "delete failed runs" on the Executions screen while a failed run links a bug → it succeeds and the linked defect is checked again → AC-2

## Commands
- [x] `pytest -q backend/tests/test_knowledge_defects.py` → 18 passed → AC-2
- [x] With Jira credentials removed (mock mode), run `python -m backend.scripts.reindex_project_knowledge --project <id>` → no `defect` entry is written, defects count as skipped → AC-2, AC-3
- [ ] Run the reindex script on a project with a requirement saved with a `jira_issue_key` → its `jira_story` entry is indexed, with no Jira call → AC-3 (local)
- [ ] With Jira credentials set, run the reindex script → each linked bug key is indexed once → AC-3 (needs Jira)

## Value sourcing checks
- [ ] Story text → the `jira_story` entry text reads `Jira story <key>: <title>`, then the description, then the requirement's acceptance criteria → AC-1 (local)
- [ ] Defect belongs to the project: give an execution `jira_bug_id` = `" BUG-1 "` (spaces) → indexed as `BUG-1`; a key `bug-1` (other case) does not match → AC-2
- [ ] Defect text: an issue with a rich text description, no priority, and an empty summary indexes without error, titled by the key → AC-2
- [ ] Jira unreachable: with an entry already indexed, point the Jira base URL at a dead host and queue a reindex → retried on `RAG_INDEX_RETRY_DELAYS`, then `failed` with "could not read"; the existing entry keeps its remote document → AC-2

## Acceptance criteria coverage
- AC-1: story import, story delete, and story text steps · AC-2: link, trim, closed bug, removal, Jira down, mock mode, field shape, isolation steps · AC-3: reindex with and without Jira
- Seen on 2026-11-18 while Jira was down (not ticked, the full step needs a working Jira): the trim of `" SCRUM-1 "` to `SCRUM-1`, and a Jira read retried twice then `failed` with "could not read SCRUM-1 from Jira".
