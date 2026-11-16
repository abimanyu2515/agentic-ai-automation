# 0005. Add an app wide chat assistant that answers over project data and runs confirmed actions

**Date**: 2026-11-12
**Status**: In Progress

## Summary

You get a floating chat icon at the bottom right of every screen. With a project selected, the assistant answers questions about that project's requirements, scenarios, test cases, and executions. It can also propose actions (generate, run, create, edit, delete). Each action shows up as a card, and nothing changes until you click Run. Deletes also need you to type DELETE. Conversations are saved per user per project through the existing repository (JSON or PostgreSQL), and replies stream in as they are written. Behind it is a plain tool calling loop (the model asks the server to run named functions) on a new `chat` LLM role, so the chat never queues behind the analysis or generation work.

## Requirements

**User stories**:
- As a QA engineer, I want to ask questions about my project from any screen, so I can find out why a test failed or what a requirement covers without hunting through views.
- As a QA engineer, I want the assistant to generate, run, create, edit, and delete things for me after I approve each one, so routine work takes one sentence and one click.
- As a QA engineer, I want my conversations saved per project, so I can pick up a thread later or on another machine.
- As a new user, I want to ask how the app works before I pick a project.

**Acceptance criteria**:
- **AC-1**: When `CHAT_ENABLED` is true, a floating chat button shows at the bottom right of every view. Clicking it opens a panel of about 400 by 600 px anchored above the button, with an expand control that widens it. Under 640 px viewport width the panel opens as a full screen sheet. The page behind stays usable.
- **AC-2**: The panel's open or closed state and the active thread ID (per project) are kept in `localStorage` as a hint. The panel lives outside the view panels, so switching views never resets it, and after a reload it reopens on the same thread, loaded from the server. If that thread returns 404 (purged or deleted elsewhere), the hint is cleared and the panel shows the empty new chat state.
- **AC-3**: With no project selected, the panel runs in help mode. Messages go to `POST /api/v1/chat/help`, answers come only from the built in app guide, and nothing is saved on the server (the browser keeps at most the last 10 messages in memory and sends them as `history`; the server keeps only the last 10 and cuts each to 4000 characters). Help mode has no project data tools and no action tools. A project question or action request gets a reply asking the user to select a project.
- **AC-4**: With a project selected, every user message sends the project ID (in the path) and a `view_context` of `{view, item_type, item_id}` taken from the current app state by the open item rules (allowed values in *View context vocabulary*). The assistant resolves phrases such as "this test case" or "why did this fail" from that context without the user naming the item. The server treats `view_context` as untrusted: an unknown `view` or `item_type` is dropped, and an `item_id` that does not belong to the project is dropped without an error.
- **AC-5**: A message sent without `thread_id` creates a thread titled with the first 80 characters of the message (whitespace collapsed). The first stream event is `thread` and carries the new thread ID. "New chat" only clears the panel and creates nothing on the server.
- **AC-6**: The thread list shows the user's threads for the selected project, most recent activity first, 20 per page with a cursor. Opening a thread loads its latest 50 visible messages, and older messages load on scroll (`before_seq`). Tool rows are never returned to the browser.
- **AC-7**: Replies stream over server sent events (SSE, a one way stream of events over a normal HTTP response), read with `fetch` and a `ReadableStream`. Events: `thread`, `message_start`, `token`, `tool`, `action`, `done`, `error`. When `done` arrives, the full assistant message is already saved.
- **AC-8**: The assistant answers questions over the selected project's data by calling the read tools in the tool catalog. Each tool call is saved as a `tool` row (name, arguments, result cut to `CHAT_TOOL_RESULT_CHARS`). A turn makes at most `CHAT_MAX_TOOL_STEPS` tool calls; past that, the assistant answers with what it has and says the answer may be incomplete.
- **AC-9**: Each turn sends the LLM the system prompt, the app guide, and the most recent messages of the thread (user, assistant, and tool rows): at most `CHAT_HISTORY_MESSAGES` messages and `CHAT_HISTORY_CHARS` characters. The oldest messages drop first, and a tool row is never sent without the assistant message that called it.
- **AC-10**: When the assistant wants to change something, it proposes exactly one action per assistant turn. The proposal shows as a card (summary, target records, what it will change) with Run and Cancel buttons, and is saved on that assistant message with status `proposed`. Nothing changes until Run is clicked.
- **AC-11**: The actions are exactly those in the action catalog. Each maps to one `(module, action)` permission. The LLM is offered only the propose tools for actions the user currently holds permission for on this project, and confirm checks the permission again. A confirm without the permission returns 403 and the card shows "You no longer have permission".
- **AC-12**: Confirm runs the parameters saved with the proposal, never parameters sent by the browser. The change from `proposed` to the next status is atomic: a second confirm or a cancel after confirm returns 409. Cancel moves a `proposed` action to `cancelled`.
- **AC-13**: Delete actions (`danger: "delete"`) list every record they will remove (name and ID) on the card. The Run button stays disabled until the user types `DELETE`. The server rejects a delete confirm whose `confirm_text` is not exactly `DELETE` with 422.
- **AC-14**: Sending a new user message in a thread moves every earlier `proposed` action in that thread to `expired`. Confirming an expired action returns 409, and its card shows "Expired, ask again".
- **AC-15**: After Run, the card shows live status (`queued`, `running`, `succeeded`, `failed`) by polling `GET .../action` every 3 seconds while the status is `queued` or `running`. The server refreshes the status from the underlying script job or execution and saves it on the message, so the card shows the right status after a reload or in another tab. A finished card links to the result (execution report, scenarios list, test case). The user can keep chatting while it runs.
- **AC-16**: When an action reaches `succeeded`, the panel dispatches a `chat:data-changed` event naming the affected views. If the current view is one of them, it reloads its data.
- **AC-17**: The user's message is always saved before the LLM is called. If the stream breaks or the LLM fails partway, the partial reply is saved with `interrupted: true` and an `error` event is sent (`code`, `message`, `retryable`). The panel shows the error and a Retry button that regenerates that turn. When the chat role pool's max wait is exceeded (spec 0003), the message reads "The assistant is busy, try again in a moment".
- **AC-18**: One turn runs at a time per thread. A second message or retry for a thread with a turn in progress returns 409. While a reply streams, the input is disabled and a Stop button shows. Stop aborts the request, the server saves the partial reply with `interrupted: true`, and the input re-enables.
- **AC-19**: Each user may send at most `CHAT_RATE_LIMIT_PER_MINUTE` chat messages (project, help, and retry combined) in any 60 second window. The limit is checked before anything is saved, and only accepted requests count (a 409 or 422 does not use up the allowance). Over the limit returns 429 and the panel shows "Too many messages, wait a moment".
- **AC-20**: Threads with no activity for `CHAT_RETENTION_DAYS` are deleted with their messages. The purge runs once at app startup for everyone (`purge_idle` with no filters), and on every thread list call for that user and project. Threads with a turn in progress are skipped. Users can delete their own threads by hand. Deleting a project deletes all its threads. Deleting a thread whose action is still running does not stop the work; the worker's final status write finds no message, logs it, and still writes the audit entry.
- **AC-21**: A user can only list, read, post to, or delete their own threads, and only on projects they can access (Superadmin or Admin, project owner, or assigned through `ProjectUserDB`). Anything else returns 404 for threads and 403 for projects.
- **AC-22**: Every confirm, cancel, and final action result writes an audit entry through `log_audit` with user, project, action type, target IDs, outcome, and `source: "chat"`.
- **AC-23**: Assistant text renders as Markdown through `marked`, then `DOMPurify.sanitize`, before it goes into the DOM. User text and card fields render through `escapeHTML`. Script or event handler markup in an LLM reply never runs. Links open in a new tab with `rel="noopener noreferrer"`.
- **AC-24**: Chat LLM calls use a new `chat` role. Any unset `CHAT_LLM_*` value falls back to the resolved analysis role value (which itself falls back to `LLM_*`, spec 0003). The chat role has its own pool. `GET /api/v1/settings` shows the chat role read only, without keys, next to `generation` and `analysis`, plus `chat_enabled`.
- **AC-25**: Chat threads and messages persist through a chat repository with a JSON store (default) and a PostgreSQL implementation. Both pass the same repository test suite.
- **AC-26**: All chat tests run with an injected LLM stub and fake action executors. No test needs a live provider or browser, and the existing suite still passes.
- **AC-27**: With a project selected, a read only line above the chat input names the context the next message will send: "About: <view label>" when no item is open, or "About: <item type label> '<name>'" when one is. It updates when the view or the open item changes. The name comes from data the screen already loaded (if it is not there, the line reads "About: <item type label>" with no name), has its whitespace collapsed, is cut to 60 characters with "…", is rendered as text, and is never sent to the server. The line is hidden when no project is selected.

## Decision

**Chosen option**: Option 1: Tool calling loop in a `ChatService`, on a dedicated `chat` LLM role, streaming over SSE, with actions stored inside assistant messages.

The chat is a service level conversation loop, not a `WorkflowState` agent. It reuses `LLMService` role pools, the repository abstraction, `log_audit`, and the existing workflow and job services to do the actual work.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### Data model sketch

**`ChatThread`** (`backend/models/chat.py`; PostgreSQL table `chat_threads`)
| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | PK | |
| `project_id` | UUID | yes | FK `projects.id`, `ON DELETE CASCADE` in PostgreSQL; JSON store deletes by project |
| `user_id` | UUID | yes | FK `users.id` (`UserDB.id` is a UUID) |
| `title` | str, max 80 | yes | first user message, whitespace collapsed, cut to 80 |
| `created_at` | datetime (UTC) | yes | |
| `last_activity_at` | datetime (UTC) | yes | set on every saved message; drives purge and list order |

Index: `(project_id, user_id, last_activity_at DESC)`. Another index on `last_activity_at` for the startup purge.

**`ChatMessage`** (table `chat_messages`)
| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | UUID | PK | |
| `thread_id` | UUID | yes | FK `chat_threads.id`, `ON DELETE CASCADE` |
| `seq` | int | yes | 1, 2, 3 within a thread; unique `(thread_id, seq)` |
| `role` | `user` / `assistant` / `tool` | yes | `tool` rows are never sent to the browser |
| `content` | text | yes | visible text; empty string on tool rows; user text max 4000 chars |
| `view_context` | JSON | nullable | user rows only: `{view: str, item_type: str or null, item_id: UUID or null}` |
| `tool_name` | str | nullable | tool rows only |
| `tool_call_id` | str | nullable | tool rows only; equals the `id` of the matching entry in the calling assistant row's `tool_calls`, pairing call and result for the LLM |
| `tool_calls` | JSON | nullable | assistant rows that called tools: `[{id, name, arguments}]` |
| `tool_args` | JSON | nullable | tool rows only |
| `tool_result` | text | nullable | tool rows only, cut to `CHAT_TOOL_RESULT_CHARS` with a `…[truncated]` marker |
| `interrupted` | bool | yes, default false | assistant rows cut short by an error or Stop |
| `action` | JSON | nullable | assistant rows that propose an action (shape below) |
| `created_at` | datetime (UTC) | yes | |

`tool_call_id`, `tool_calls`, and `interrupted` are part of the confirmed model. The first two are needed to replay tool calls to the LLM in the format LiteLLM expects, and `interrupted` comes from the failure handling decision.

**`action` JSON** (on the assistant message):
```json
{
  "action_id": "uuid",
  "type": "run_test_case",
  "params": {"test_case_id": "uuid"},
  "summary": "Run test case 'Login with valid user'",
  "targets": [{"type": "test_case", "id": "uuid", "name": "Login with valid user"}],
  "danger": "none | change | delete",
  "permission": {"module": "Executions", "action": "create"},
  "affects": ["executions"],
  "status": "proposed",
  "job_ref": {"kind": "execution | script_job | batch | inline", "id": "uuid or null"},
  "result": {"summary": "Passed in 42 s", "link_view": "executions", "link_id": "uuid"},
  "error": null,
  "confirmed_by": null,
  "updated_at": "ISO 8601"
}
```

Relationships: Project 1:N ChatThread, User 1:N ChatThread, ChatThread 1:N ChatMessage.

**Repository** (`backend/repository/chat_repository.py` interface, `JsonChatRepository` storing to `backend/database/chat_store.json` under a process lock, and `PostgresChatRepository`; picked by the same provider setting as `get_project_repository()`, exposed as `get_chat_repository()`):
`create_thread`, `get_thread`, `list_threads(project_id, user_id, cursor, limit)`, `delete_thread`, `delete_threads_for_project`, `purge_idle(before, project_id=None, user_id=None)`, `append_message` (assigns the next `seq` and bumps `last_activity_at` in the same write), `list_messages(thread_id, before_seq, limit, include_tool)`, `recent_messages_for_llm(thread_id, max_messages)`, `get_message`, `update_message_content(message_id, content, interrupted)`, `compare_and_set_action_status(message_id, expected: set[str], new_status, patch: dict) -> action | None`, `expire_proposed_actions(thread_id)`.

`compare_and_set_action_status` is the only way an action's status changes. In PostgreSQL it is a single `UPDATE … WHERE id = :id AND action->>'status' = ANY(:expected) RETURNING action`. In JSON it runs under the store lock. On any SQLAlchemy error the repository rolls back and removes the session before raising (AGENTS.md rule).

### State transitions

Action status:
- `proposed` → `queued` or `running` (confirm; `queued` for script jobs and batches until the job reports running)
- `queued` → `running` → `succeeded` | `failed` (refreshed from the job or execution)
- `proposed` → `cancelled` (cancel)
- `proposed` → `expired` (a new user message in the same thread)
- `succeeded`, `failed`, `cancelled`, `expired` are terminal. Any other change returns 409.

Turn lifecycle (in memory, per thread): idle → streaming → idle. A thread ID sits in the in progress set from the moment the user message is saved until the stream ends for any reason (done, error, client disconnect).

### Tool catalog

Read tools (all scoped to the path's `project_id`; every ID argument is checked to belong to that project, otherwise the tool returns "not found in this project"):
| Tool | Returns |
|---|---|
| `get_project_overview` | name, target URL, framework, counts of requirements, scenarios, test cases, executions; never credentials |
| `list_requirements(query?, limit≤50)` | id, title, status |
| `get_requirement(requirement_id)` | full requirement plus its scenario IDs |
| `list_scenarios(requirement_id?, limit≤50)` | id, title, requirement ID, status |
| `list_test_cases(scenario_id?, requirement_id?, query?, limit≤50)` | id, title, has script, last execution status |
| `get_test_case(test_case_id)` | steps, expected results, data, script present or not (script body cut to 2000 chars); any test data key matching `password`, `passwd`, `token`, `secret`, or `key` (case insensitive) has its value replaced with `"[redacted]"` |
| `list_executions(status?, test_case_id?, limit≤50)` | id, test case, status, started, duration |
| `get_execution(execution_id)` | status, error message, failed step, analysis summary, artifact availability (no file contents) |
| `get_dashboard_metrics` | the same data as `GET /projects/{id}/dashboard-metrics` |
| `list_object_repository_pages(limit≤50)` | page names and element counts |

All `list_*` tools default to `limit=20` and accept at most 50. `get_dashboard_metrics` returns the fields of `DashboardMetricsResponse` as is. When a result is longer than `CHAT_TOOL_RESULT_CHARS`, rows are dropped from the end of the list (never cut mid JSON) and `"truncated": true` plus `"total": N` are added; a single record that is still too long has its longest text fields cut, each ending in `…[truncated]`.

Propose tools (one per action type), named `propose_<type>` (for example `propose_run_test_case`). Their JSON schemas are generated from a pydantic params model per type in `backend/services/chat_actions.py`. Edit types take `{<id field>, changes: {field: new_value}}`, where `changes` only allows the fields listed in the action catalog. Calling a propose tool never runs the action. The server validates the parameters, resolves `targets` names, fills in `danger`, `permission`, and `affects`, saves the `action` on the current assistant message, sends an `action` event, and ends the turn (rules in *Turn rules*).

### Action catalog

| Type | Params | Permission | Danger | Runs through | Status from |
|---|---|---|---|---|---|
| `generate_scenarios` | `requirement_id`, `count` 1..10 (default 3), `mode` `append`/`replace`; for `replace`, `replace_ids` (filled by the server at propose time) | Scenarios/create | `change` (`delete` when `mode=replace`, listing the scenarios it replaces) | `workflow_service.generate_scenarios` with `mode="append"` in a worker thread; for `replace`, the old scenarios in `replace_ids` are deleted only after generation succeeds | `inline` |
| `generate_test_cases` | `requirement_id` | TestCases/create | `change` | `workflow_service.generate_test_cases` in a worker thread | `inline` |
| `generate_script` | `test_case_id`, `framework?` | Playwright/create | `change` | `script_generation_jobs.start` | `script_job` |
| `run_test_case` | `test_case_id` | Executions/create | `change` | new `execution_service.start_test_case_run` (taken out of the execute route) | `execution` |
| `run_batch` | `test_case_ids` (1..50) | Executions/create | `change` | new `execution_service.start_batch_run` (taken out of the batch route) | `batch` |
| `create_requirement` | `title`, `description`, optional fields `save_requirement` accepts | Requirements/create | `change` | `project_service.save_requirement` | `inline` |
| `update_requirement` | `requirement_id`, changed fields | Requirements/edit | `change` | `project_service.save_requirement` | `inline` |
| `delete_requirement` | `requirement_id` | Requirements/delete | `delete` (lists the requirement and the counts of its scenarios and test cases) | new `project_service.delete_requirement` | `inline` |
| `update_scenario` | `scenario_id`, changed fields | Scenarios/edit | `change` | the service the `PUT /scenarios/{id}` route uses | `inline` |
| `duplicate_scenario` | `scenario_id` | Scenarios/create | `change` | the service the duplicate route uses | `inline` |
| `delete_scenario` | `scenario_id` | Scenarios/delete | `delete` | `repo.delete_scenario(deleted_by=user.id)` | `inline` |
| `update_test_case` | `test_case_id`, changed fields (title, steps, expected results, data) | TestCases/edit | `change` | `repo.update_test_case` | `inline` |
| `review_test_case` | `test_case_id`, `decision` `approved`/`rejected` | TestCases/edit | `change` | `project_service.update_test_case(evaluation_status=...)`, the call the approve and reject buttons use | `inline` |
| `generate_test_data` | `test_case_id` (Playwright project and test case only, checked at propose time) | TestCases/edit | `change` | `workflow_service.generate_test_data_for_test_case` in a worker thread; a script that no longer matches the new datasets is cleared, as on the Test Cases screen | `inline` |
| `delete_test_case` | `test_case_id` | TestCases/delete | `delete` | `repo.delete_test_case(deleted_by=user.id)` | `inline` |
| `delete_execution` | `execution_id` | Executions/delete | `delete` | `repo.delete_execution(deleted_by=user.id)` | `inline` |
| `delete_failed_executions` | `execution_ids` (filled by the server at propose time with the project's current failed executions, at most 200) | Executions/delete | `delete` (lists the count and up to 20 names) | `repo.delete_execution` for each ID in `execution_ids` only; executions that failed after the proposal are left alone | `inline` |

`affects` per type: generate scenarios → `scenarios`; generate test cases → `testcases`; script → `playwright`, `testcases`; runs → `executions`, `reports`; requirement actions → `requirements`; scenario actions → `scenarios`; test case actions → `testcases` (test data also `playwright`, since it may clear a script); execution deletes → `executions`, `reports`.

Every `edit` action's card shows each changed field as old value and new value. Edits are validated with the same pydantic DTOs the matching routes use (`backend/schemas/`).

`inline` actions run in a worker thread started by confirm. The worker sets the final status through `compare_and_set_action_status`, so the status endpoint just reads it. `script_job`, `execution`, and `batch` statuses are pulled from `script_generation_jobs`, the execution repository, and the batch queue manager when the status endpoint is called.

**Result and status mapping** (the action status says whether the action ran; a test verdict goes in the summary):
| Type | `job_ref` | Status source and mapping | `result.summary` | `result.link_view` / `link_id` |
|---|---|---|---|---|
| `generate_scenarios` | `inline`, null | worker: `running` until return; return → `succeeded`, exception → `failed` | "Created N scenarios" (plus "replaced M") | `scenarios` / `requirement_id` |
| `generate_test_cases` | `inline`, null | as above | "Created N test cases" | `testcases` / `requirement_id` |
| `generate_script` | `script_job`, job ID | job `RUNNING` → `running`, `COMPLETED` → `succeeded`, `FAILED` → `failed` (job `error` into `action.error`); unknown job ID → `failed` "lost on restart" | "Script generated" | `playwright` / `test_case_id` |
| `run_test_case` | `execution`, execution ID | no execution record and the ID is in `active_execution_events` → `running`; record `passed` or `failed` → `succeeded`; record `error` → `failed`; no record and no live events → `failed` "lost on restart" | "Passed in N s" or "Failed at step N: <first 120 chars of error>" | `executions` / execution ID |
| `run_batch` | `batch`, batch ID | any item `queued` → `queued`; any item `running` → `running`; every item has a verdict → `succeeded`; batch not in `active_batch_events` and not finished → `failed` "lost on restart" | "N passed, M failed, K errors" | `executions` / batch ID |
| create and edit types | `inline`, null | worker, as above | "Created requirement 'X'" / "Updated N fields on 'X'" | the record's view / record ID |
| delete types | `inline`, null | worker, as above | "Deleted N records" | the list view / null |

**Confirm rules** (in this order): owner check → `check_permission` (403) → `confirm_text` for deletes (422) → recheck that every target ID still exists and belongs to the project, and that `params` still pass the params model (422 "This item changed or no longer exists, ask again") → `compare_and_set_action_status(expected={"proposed"})` (409 if lost) → start the work → audit entry. Edits overwrite only the fields in `changes` with the proposed values, even if someone changed them since the proposal; the card says "will set" values, not a merge.

**Status writers**: the worker thread, the status endpoint, cancel, and startup recovery all change status only through `compare_and_set_action_status`, with `expected` set to the status they last read. A writer that loses rereads and returns the current action. The write that first moves an action into a terminal status also writes the final result audit entry, whoever makes it.

**Startup recovery**: at startup, after the purge, every action still `queued` or `running` is refreshed once. `inline` actions (whose worker died with the process) and job backed actions whose job is gone become `failed` with error "Interrupted by a server restart". Any assistant row left empty by a turn cut off by the restart is marked `interrupted`.

**Card rendering**: the card's main line and target list are built by the frontend from the server's `type`, `targets`, and (for edits) the old and new values. The model written `summary` shows only as smaller secondary text, so injected text cannot relabel what will run.

### View context vocabulary

`getChatContext()` in `app.js` returns one of these. `item_type` and `item_id` are null when nothing is open.
| `view` | `item_type` when an item is open | Which item |
|---|---|---|
| `dashboard`, `project`, `settings`, `langsmith`, `jira-traceability`, `reports`, `requirements` | null | none. The Requirements screen is a plain list with no detail or edit form, so nothing is open there; the user names the requirement instead |
| `scenarios`, `scenario-approval` | `scenario`, else `requirement` | the open scenario row (open item rules); if none, the requirement selected in the screen's requirement dropdown |
| `testcases`, `testcase-approval` | `test_case`, else `requirement` | the open test case row (open item rules); if none, the requirement selected in the screen's requirement dropdown |
| `testdatagent` | `test_case` | the test case open for test data |
| `playwright` | `test_case` | the test case whose script is open (`activePlaywrightTestCaseId`) |
| `execution-workspace` | `execution` on `#/execution/<id>`; `test_case` on `#/execution-workspace` | the execution in the route; otherwise the test case whose script is open |
| `executions` | `execution` | the execution whose triage details modal is open |
| `object-repository` | `object_repository_page` | the selected page |

**Open item rules** (scenario and test case rows, and the triage modal):
- **One entry per row.** A scenario or test case row is open while it is expanded or in inline edit (either one). Opening it, or entering edit on a row that is already expanded, makes it the most recent. Collapsing a row that is still in edit keeps it open.
- **Where state is recorded.** `app.js` wraps `window.toggleScenarioRow` and `window.toggleTestCaseRow`, and calls explicit open and close hooks from the edit, cancel, save, and delete handlers. State is never inferred by watching the DOM.
- **Leaving edit.** Cancel, a successful save, and a successful delete end edit for that row. A failed save leaves it in edit.
- **Most recent wins.** When several rows are open, the most recently opened one still open is the item. Closing it falls back to the previous one still open, then to the selected requirement.
- **Cleared on.** A full re-render of that view's list (any `refreshProjectData` driven render: after a re-render nothing is open, matching the screen), a change of that view's requirement dropdown, a delete of that row, and a project change.
- **Per view.** Each view (`scenarios`, `scenario-approval`, `testcases`, `testcase-approval`) keeps its own open row list, under the same rules.
- **Requirement fallback.** The value is `selectedRequirementId` (the dropdown on these screens and the approval screens all write it), used only when it is in the selected project's loaded `requirements`; otherwise no item is sent. Because the dropdown defaults to the first requirement, the fallback is present on these screens in almost every case. That is intended.
- **Executions.** An execution is open while the triage modal is open. Every close path goes through `closeModal('triage-modal')`, which clears it, and a route change clears it too.
- **Memory only, per tab.** Open rows live in memory, never in `localStorage`. Each browser tab has its own open item, while the active thread hint (AC-2) is shared per project across tabs.
- **When it is read.** `getChatContext()` is called at the moment a message is sent. Whenever the context can change (route change, a row opening or closing, the triage modal opening or closing, a requirement dropdown change, a project change), `app.js` dispatches one `chat:context-changed` event, and `chat.js` redraws the context line (AC-27) from `getChatContext()`.
- **Module.** The open row bookkeeping (per view ordered lists, open, close, edit flags, clear, current item) lives in a small pure module `frontend/js/chat-context.js` with no DOM access, so its rules can be checked on their own.

The server keeps this table as an allow list of `(view, item_type)` pairs; any other pair is dropped as an unknown item type. The current `view_context` goes into the system prompt. Each replayed user message also carries its own context as a first line `[viewing: <view> <item_type> <item_id>]`, so old turns do not borrow the current screen.

### Turn rules

- **Rows written per turn**: the user row; for each LLM step that calls tools, one hidden assistant row holding `tool_calls` (plus any text from that step) and one `tool` row per call; and one visible assistant row. The visible row is created empty when `message_start` is sent (its ID is `assistant_message_id`), and its text is saved with `update_message_content` at each tool boundary, at the end, and on interruption. It is never saved per token.
- **Proposing**: when a step's tool calls include a propose call, the server takes the first propose call, saves a `tool` row for it with result `"proposed, awaiting user confirmation"`, and saves `tool` rows with result `"error: only one action per turn"` for any other propose calls. Read calls in the same step still run and are saved. Text streamed before the propose stays in the visible row. The `action` goes on the visible assistant row, and the turn ends.
- **Step cap**: the cap applies to read tool steps only. A propose call always ends the turn, even on the last allowed step. When the cap is hit with no proposal, one final call with no tools produces the answer.
- **History sent to the LLM** (AC-9): the system prompt and app guide are not counted in `CHAT_HISTORY_CHARS`; the guide has its own cap `CHAT_GUIDE_CHARS`, checked at startup (the app logs an error and cuts the guide if it is longer). History is taken newest first, a hidden assistant `tool_calls` row and its `tool` rows count as one unit and are kept or dropped together, and a unit that would leave a `tool` row without its call is dropped whole. Interrupted replies are sent with `[interrupted]` appended. An assistant row with an action is sent with `[action <type> on <target names>: <status>]` appended.
- **Disconnects**: the loop checks for a client disconnect before each LLM step, before each tool call, and between chunks. On a disconnect it saves the visible row as `interrupted` and releases the thread lock and the stream slot.
- **Retry**: allowed only when the last visible row in the thread is an assistant row with no action, or with an action still `proposed` (which becomes `expired`), or when the last row is a user row with no reply. The old assistant row and its hidden rows from that turn are deleted, and the new turn appends new rows with new `seq` values (gaps in `seq` are fine). Otherwise 409.
- **Stream cap**: at most `CHAT_MAX_STREAMS` turns stream at once in the process (each holds a threadpool thread and a pool slot). Above that, the request gets an immediate `error` event `llm_busy`.
- **JSON store writes**: each write goes to a temp file in the same folder, then an atomic rename, under the store lock.
- **Single process check**: at startup, if more than one worker is configured (`WEB_CONCURRENCY` or the uvicorn `workers` setting above 1), log a warning that chat locks, rate limits, and role pools are per process.

### Pagination

- Threads: `cursor` is an opaque base64 string of `last_activity_at` plus `id`, ordered by `last_activity_at DESC, id DESC`.
- Messages: tool rows and hidden assistant `tool_calls` rows are filtered out first. `before_seq` then returns visible rows with `seq` lower than it, newest first, up to `limit` (default 50, max 100).

### API surface

Prefix `P` = `/api/v1/projects/{project_id}/chat`. All endpoints need a bearer token (`get_current_user`) and project access (AC-21).

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `P/messages` | POST | `thread_id`: UUID (opt), `content`: str 1..4000 (req), `view_context`: object (opt) | `text/event-stream` (events below) | user, project access | 404 thread not yours, 409 turn in progress, 422 content, 429 rate limit |
| `P/threads/{thread_id}/retry` | POST | none (regenerates the last assistant turn; an interrupted reply is replaced) | `text/event-stream` | owner | 404, 409 turn in progress or last message not a user turn or interrupted reply, 429 |
| `P/threads` | GET | `cursor`: str (opt), `limit` ≤ 50 (default 20) | `{items: [{id, title, last_activity_at}], next_cursor}` | user, project access | 403 |
| `P/threads/{thread_id}/messages` | GET | `before_seq`: int (opt), `limit` ≤ 100 (default 50) | `{items: [{id, seq, role, content, interrupted, action, created_at}], has_more}` | owner | 404 |
| `P/threads/{thread_id}` | DELETE | none | 204 | owner | 404, 409 turn in progress |
| `P/messages/{message_id}/action/confirm` | POST | `confirm_text`: str (required when `danger=delete`) | the updated `action` | owner plus action permission | 403 permission, 404, 409 not `proposed` (expired, cancelled, already confirmed), 422 wrong `confirm_text` or params no longer valid |
| `P/messages/{message_id}/action/cancel` | POST | none | the updated `action` | owner | 404, 409 not `proposed` |
| `P/messages/{message_id}/action` | GET | none | the `action`, refreshed from its job | owner | 404 |
| `/api/v1/chat/help` | POST | `content`: str 1..4000, `history`: `[{role: user/assistant, content}]` ≤ 20 items | `text/event-stream` (`message_start`, `token`, `done`, `error` only) | user | 422, 429 |
| `/api/v1/settings` | GET (existing) | none | adds `llm_roles.chat` and `chat_enabled` | user | none |

When `CHAT_ENABLED` is false, every chat route returns 404 and the frontend does not render the button. The frontend reads `chat_enabled` from `GET /api/v1/settings`, which it calls right after login (and on page load when a token exists) before rendering the button. The login page never shows it.

SSE events (`event:` name, JSON `data:`):
- `thread` `{thread_id, title}` (only when a thread was just created)
- `message_start` `{user_message_id, assistant_message_id}`
- `token` `{text}`
- `tool` `{name, state: "start" | "end"}` (the panel shows "Looking up test cases…")
- `action` `{message_id, action}`
- `done` `{assistant_message_id}`
- `error` `{code: "llm_busy" | "llm_error" | "tool_error" | "internal", message, retryable: bool}`

The server sends a `: ping` comment every 15 seconds while a tool runs, so proxies keep the stream open.

### Value sourcing

| Action | Value produced or displayed | Source |
|---|---|---|
| Any project chat call | project ID | path param `project_id` |
| Any project chat call | user ID | `get_current_user` from the bearer token |
| Send message | current view and item | `view_context`, built by `getChatContext()` in `app.js` from the route and the open item rules in *View context vocabulary* (open row stack per view, the requirement dropdown value, `activePlaywrightTestCaseId`, the triage modal's execution, the selected object repository page) |
| Context line (AC-27) | view label | the text of the sidebar nav item for that view (`.nav-item[data-view]`); the `view` key when the view has no nav item (for example `execution-workspace`) |
| Context line (AC-27) | item type label | fixed labels in `chat.js`: requirement, scenario, test case, execution, page |
| Context line (AC-27) | item name | the item's title from the data the screen already loaded (`requirements`, `scenarios`, `testCases` arrays in `app.js`, or the object repository view's pages). An execution has no title: its name is its test case's title from `testCases`, else the first 8 characters of its ID. The type label alone when nothing is loaded |
| Send message | thread title | derived from the first user message's `content` |
| Send message | messages sent to the LLM | `recent_messages_for_llm`, cut by `CHAT_HISTORY_MESSAGES` and `CHAT_HISTORY_CHARS` |
| Send message | app guide text | file `backend/prompts/agents/chat_assistant/app_guide.md` (hand written from `docs/workflow.md` and `docs/guides/`) |
| Send message | system prompt | file `backend/prompts/agents/chat_assistant/system_prompt.txt`, filled with project name, current date, and `view_context` |
| Send message | which propose tools are offered | the action catalog filtered by `check_permission(user, project_id, module, action)` |
| Propose action | `targets` names and delete lists | read from the repository by the IDs in `params` at propose time |
| Propose action | old values on edit cards | read from the repository at propose time |
| Confirm | parameters to run | the saved `action.params`, never the request body |
| Confirm | permission decision | `check_permission` at confirm time |
| Status card | status, result summary, link | `action.status` and `action.result`, refreshed from `script_generation_jobs`, the execution repository, or the batch queue manager by `job_ref` |
| Status card | which view to reload | `action.affects` |
| Thread list | order | `last_activity_at` |
| Purge | cutoff | now (UTC) minus `CHAT_RETENTION_DAYS` |
| Rate limit | per user count | in memory sliding window keyed by user ID |
| Help mode | prior turns | `history` in the request body, held by the browser |
| Settings view | chat role values | `settings.llm_roles["chat"]` |

### Key invariants

- Every stored thread has a `project_id` and a `user_id`. A thread is only ever read or written by its owner.
- An action's status changes only through `compare_and_set_action_status`. At most one confirm can succeed per action.
- Confirm runs `action.params` as saved. The browser can send only `confirm_text`.
- At most one action per assistant message, and at most one turn in progress per thread.
- Every ID a tool or action touches belongs to the path's project. A cross project ID is treated as not found.
- No tool returns credentials, vault data, passwords, or API keys. `get_project_overview` reads only the masked project DTO.
- A `tool` row is never sent to the browser.
- The user message is saved before any LLM call.

### Security model

- **Authentication**: bearer token through `get_current_user` on every chat route, the same as the rest of `/api/v1`.
- **Project access**: Superadmin or Admin, the project owner, or a `ProjectUserDB` assignment, the same rule the script generation route already uses. Take that check out into one shared helper `require_project_access(user, project_id, db)` that the chat routes call.
- **Thread ownership**: a thread whose `user_id` is not the caller's returns 404 (it does not reveal that the thread exists).
- **Action permissions**: take the body of `has_permission` out into a plain function `check_permission(db, user, project_id, module, action) -> bool` (project role first, then the global role, Superadmin always allowed). `has_permission` keeps working by calling it. The chat filters propose tools with it and checks again at confirm.
- **Prompt injection**: requirement text, test case data, execution errors, and page data are untrusted input to the LLM. The system prompt tells the model to treat tool results as data, but the real guard is structural: the model can only propose, the card shows exactly what will run, the user must click Run, and deletes need `DELETE` typed.
- **Output safety**: assistant Markdown goes through `marked` and then `DOMPurify`. Everything else goes through `escapeHTML`.
- **Abuse**: per user rate limit (AC-19). Content is capped at 4000 characters.
- **Audit**: `log_audit` on confirm, cancel, and final result (AC-22).
- **Compliance scope**: none beyond the app's existing scope. Chat history can hold project details users type in, and it is deleted by retention and with the project.

### Configuration required

- `CHAT_ENABLED` (default `true`): turns the button and the chat routes on or off.
- `CHAT_LLM_PROVIDER`, `CHAT_LLM_MODEL`, `CHAT_LLM_API_KEY`, `CHAT_LLM_API_BASE`: the chat role. Unset values fall back to the analysis role. The model must support tool calling and streaming; if the analysis model does not, set `CHAT_LLM_MODEL`.
- `CHAT_LLM_MAX_CONCURRENT` (default 4), `CHAT_LLM_COOLDOWN_SECONDS` (default 60), `CHAT_LLM_MAX_WAIT_SECONDS` (default 30): chat pool settings, same meaning as spec 0003. The short max wait means a busy pool fails fast with `llm_busy` instead of leaving a user staring at the panel.
- `CHAT_HISTORY_MESSAGES` (default 20), `CHAT_HISTORY_CHARS` (default 24000)
- `CHAT_MAX_TOOL_STEPS` (default 6), `CHAT_TOOL_RESULT_CHARS` (default 4000)
- `CHAT_GUIDE_CHARS` (default 8000): cap on the app guide in the prompt
- `CHAT_MAX_STREAMS` (default 10): turns allowed to stream at once in the process
- `CHAT_RATE_LIMIT_PER_MINUTE` (default 20)
- `CHAT_RETENTION_DAYS` (default 90)

Defaults go in `backend/config/default_config.yaml` under `chat:`. Keys and per machine values go in `.env`.

### Implementation notes (decided here so the build does not invent them)

- **LLM streaming**: add `LLMService.stream_chat(messages, tools) -> Iterator[ChatChunk]` that calls LiteLLM `completion(..., stream=True, tools=..., num_retries=0)` through the role pool. It holds one pool slot for the whole stream and applies the same 429 cooldown and max wait (counted until the first chunk). `ChatChunk` is either text or a finished tool call (streamed tool call pieces are put back together inside `stream_chat`). Stubs in tests yield scripted chunks.
- **The loop** (`backend/services/chat_service.py`, `ChatService.run_turn`): save the user message, expire proposed actions, build the prompt, then repeat: stream from the LLM, forward text as `token` events, run each read tool call (saving the assistant `tool_calls` row and a `tool` row per result), stop on a propose tool or when the model answers with text only. When the step cap is reached, call the model one last time with no tools. `ChatService` takes `llm_service`, `chat_repository`, `project_repository`, a `ChatToolbox`, and a `ChatActionExecutor` through its constructor, so tests inject doubles.
- **Sync stream in FastAPI**: `run_turn` is a generator run through `StreamingResponse`. A client disconnect is detected with `await request.is_disconnected()` between chunks (async wrapper around the sync iterator with `iterate_in_threadpool`). On disconnect, save the partial text with `interrupted=true` and release the thread lock in a `finally`.
- **Thread lock, stream cap, and rate limiter**: in process structures (a set guarded by a lock, a semaphore, and a deque of timestamps per user). They assume one app process, like the role pools in spec 0003.
- **Run attribution**: `execution_service.start_test_case_run(project_id, test_case_id)` and `start_batch_run(project_id, test_case_ids)` take the same inputs the routes use today and return the new execution or batch ID. Project config and vault credentials are resolved inside, exactly as the routes do now (captured before queueing, per `backend/AGENTS.md`). The chat records who started the run in `action.confirmed_by` and the audit entry; no new field is added to execution results.
- **Frontend**: new `frontend/js/chat.js` (button, panel, thread list, stream reader, cards, status polling), API wrappers in `frontend/js/api.js` including a `streamRequest(path, body, onEvent, signal)` helper that uses the same token and refresh logic as `request` (refresh once on 401 before the stream starts), styles in a `/* Chat */` section of `frontend/css/styles.css`, and two pinned script tags in `index.html` for `marked` and `DOMPurify` from cdnjs with exact versions. `app.js` exposes `getChatContext()` and listens for `chat:data-changed`.

### Critical test scenarios

- Happy path: user on a test case view asks "why did this fail?", the assistant calls `get_execution` through a stubbed LLM, streams an answer, and the thread, user, tool, and assistant rows are saved. Verifies **AC-4**, **AC-5**, **AC-7**, **AC-8**.
- Happy path action: the assistant proposes `run_test_case`, the user confirms, status moves `running` → `succeeded` from a fake execution, and `chat:data-changed` names `executions`. Verifies **AC-10**, **AC-12**, **AC-15**, **AC-16**.
- Delete: `delete_test_case` confirm with no `confirm_text` returns 422; with `DELETE` it succeeds and writes an audit entry. Verifies **AC-13**, **AC-22**.
- Double confirm: two concurrent confirms on one action, exactly one returns 200 and the other 409, on both JSON and PostgreSQL. Verifies **AC-12**, **AC-25**.
- Expiry: propose, send another message, confirm the first card, get 409. Verifies **AC-14**.
- Browser supplied params ignored: confirm body with extra `params` runs the saved params. Verifies **AC-12**.
- LLM failure halfway: stub raises after three tokens, partial reply saved with `interrupted=true`, `error` event `retryable: true`, and retry replaces it. Pool max wait returns `llm_busy`. Verifies **AC-17**.
- Concurrency: second POST to a streaming thread returns 409, and a client disconnect saves the partial reply. Verifies **AC-18**.
- Permission: a user without `Executions/create` gets no `propose_run_test_case` tool, and losing the permission between propose and confirm returns 403. Verifies **AC-11**.
- Isolation: user B cannot read user A's thread (404). A tool call with a test case ID from another project returns "not found in this project". Verifies **AC-21**, invariants.
- Retention: a thread idle 91 days is purged on list, and deleting the project removes its threads. Verifies **AC-20**.
- XSS: an LLM reply containing `<img src=x onerror=alert(1)>` renders with no handler. Verifies **AC-23**.
- Rate limit: the 21st message within 60 seconds returns 429, and a 409 does not count. Verifies **AC-19**.
- Restart: an `inline` action left `running` becomes `failed` "Interrupted by a server restart" after startup recovery. Verifies **AC-15**.
- Replace safety: `generate_scenarios` with `mode=replace` whose generation fails leaves the old scenarios in place. Verifies **AC-15**.
- Untrusted context: a `view_context.item_id` from another project is dropped and never reaches the prompt. Verifies **AC-4**.
- Open item fallback: on Test Cases, expand test case A then B → context is B; collapse B → A; collapse A → the selected requirement; switch project → no item. A `(testcases, execution)` pair is dropped by the server. Verifies **AC-4**.
- Context line: open a scenario row → the line reads "About: scenario '<its name>'"; close it → "About: requirement '<name>'"; a title containing `<b>` shows as literal text; a 100 character title is cut to 60 with "…". Verifies **AC-27**.
- Re-render: expand a test case row, then trigger `refreshProjectData` (approve another test case) → nothing is open, and the context falls back to the requirement. Verifies **AC-4**.
- Edit cancel: put a scenario in edit, collapse it (still open), then cancel the edit → it is no longer open. A failed save keeps it open. Verifies **AC-4**.
- Triage close: open the triage modal → item is that execution; close it with the close button, the Close button, or a route change → no item. Verifies **AC-4**.

These four, and the open item fallback above, are browser behaviors: they run under `/check verify` in the real app, not in the pytest suite (AC-26 covers the backend tests).
- Two proposes in one step: only the first becomes a card, and the history replayed next turn is valid for LiteLLM. Verifies **AC-10**.
- Help mode: with no project, the help endpoint answers from the guide and offers no tools. Verifies **AC-3**.

## Build plan

Ordered as tracer bullet slices (project default from `AGENTS.md`): slice 1 carries one plain question end to end through the UI, API, service, LLM, and both stores; later slices add depth.

**Slice 1: one streamed answer, end to end**
1. [x] Add the `chat` role to settings with the fallback to the analysis role, the `chat:` config block and `CHAT_*` env vars, and `llm_roles.chat` plus `chat_enabled` in `GET /api/v1/settings`, satisfies **AC-24**
2. [x] Add `LLMService.stream_chat` through the role pool (text only for now), with a stub for tests, satisfies **AC-7**, **AC-24**, **AC-26**
3. [x] Add `ChatThread` and `ChatMessage` models, the chat repository interface, `JsonChatRepository`, `PostgresChatRepository`, the Alembic migration for `chat_threads` and `chat_messages` (the full confirmed model in one migration), and `get_chat_repository()`, satisfies **AC-25**
4. [x] Take out `require_project_access` and `check_permission` into shared helpers, keeping `has_permission` and the script generation route behavior the same, satisfies **AC-21**, **AC-11**
5. [x] Build `ChatService.run_turn` without tools (save the user message, history window, system prompt plus app guide, stream, save the reply), and the `POST P/messages`, `GET P/threads`, `GET P/threads/{id}/messages` routes as thin wrappers, satisfies **AC-5**, **AC-6**, **AC-7**, **AC-9**, **AC-21**
6. [x] Build the floating button, the panel (desktop size, expand, full screen under 640 px), thread list, New chat, `streamRequest`, Markdown through pinned `marked` and `DOMPurify`, `localStorage` hints, and `getChatContext()` in `app.js` with the open item rules (open row tracking on Scenarios and Test Cases, the requirement fallback, the triage modal execution, `#/execution/<id>` as an execution), the pure open row module `frontend/js/chat-context.js`, the `chat:context-changed` event, the server allow list as `(view, item_type)` pairs (`VIEW_ITEM_TYPES` in `chat_service.py`, with the project membership check applying to the requirement fallback too), and the read only context line, satisfies **AC-1**, **AC-2**, **AC-4**, **AC-23**, **AC-27**

**Slice 2: answers over project data**
7. [x] Build `ChatToolbox` with the read tools and project ID checks, tool call reassembly in `stream_chat`, tool and `tool_calls` rows, the step cap, the result cap, and `tool` SSE events, satisfies **AC-8**, **AC-9**

**Slice 3: one action end to end (`generate_scenarios`)**
8. [x] Add propose tools filtered by permission, action validation and target resolution, the `action` event, and the card UI with Run and Cancel, satisfies **AC-10**, **AC-11**
9. [x] Add `compare_and_set_action_status`, the confirm, cancel, and status routes (confirm rules and status writers as specified), `ChatActionExecutor` running `inline` actions in a worker thread, expiry on new messages, `log_audit` calls, status polling, startup recovery, and the `chat:data-changed` reload in `app.js`, satisfies **AC-12**, **AC-14**, **AC-15**, **AC-16**, **AC-22**

> Release point: after slice 3 the feature is usable on its own (reads plus one action). Slices 4 to 7 can ship later without rework.

**Slice 4: the rest of the catalog**
10. [x] Move single and batch execution startup into `execution_service` (execution routes call the service), then add `run_test_case`, `run_batch`, `generate_script`, and `generate_test_cases` with status refresh by `job_ref`, satisfies **AC-11**, **AC-15**
11. [x] Add `project_service.delete_requirement` (both repositories; deletes its scenarios and test cases the same way scenario deletes do) and the create, update, duplicate, and delete actions for requirements, scenarios, test cases, and executions, with old and new values on edit cards, satisfies **AC-11**, **AC-13**
12. [x] Add the delete card (target list, type `DELETE` to enable Run) and the server `confirm_text` check, satisfies **AC-13**

**Slice 5: failure, concurrency, limits**
13. [x] Add the per thread turn lock, the stream cap, Stop (abort plus disconnect checks at every step), interrupted saves, `error` events, the retry route and button (retry rules as specified), the `llm_busy` mapping, and the single process warning, satisfies **AC-17**, **AC-18**
14. [x] Add the per user rate limiter across project, help, and retry routes, satisfies **AC-19**

**Slice 6: help mode and housekeeping**
15. [x] Add `POST /api/v1/chat/help` (no tools, guide only, browser held history) and the panel's no project mode, satisfies **AC-3**
16. [x] Add the startup purge, the purge on thread list, `DELETE P/threads/{id}`, and thread deletion in `project_service.delete_project`, satisfies **AC-20**
17. [x] Hide the button and return 404 from chat routes when `CHAT_ENABLED` is false, satisfies **AC-1**, **AC-24**

**Slice 7: tests and docs**
18. [x] Add focused pytest suites for chat repositories, service/route behavior, actions, and batch execution on stubs and fakes, covering critical scenarios, satisfies **AC-26** and the scenarios above
19. [x] Update `docs/api.md` (chat routes and SSE events) and the chat role in the LLM settings docs, per the documentation rule in `AGENTS.md`, satisfies **AC-24**

## Consequences

**Positive**:
- One place to ask about and act on a project from any screen. Common chores (rerun, generate, tidy up) take a sentence and a click.
- Moving execution start and batch start into `execution_service` thins `main.py`, which the routes rule already asks for.
- A shared `check_permission` makes it easy to add the missing permission checks to existing routes later.

**Negative / tradeoffs**:
- A large first version: 15 action types, deletes included. Each one is a path that can change data, and each needs its own tests.
- The chat checks permissions that many matching UI routes do not check today (for example generate scenarios and execute have no `has_permission`). A user may be refused in chat but allowed in the UI until those routes are fixed.
- Keeping the action inside the message JSON means status updates rewrite a JSON column, and finding an action needs its message ID (hence the message ID in the action URLs).
- The turn lock, rate limiter, and role pool live in process memory. Running more than one app worker breaks the "one turn per thread" and rate limit guarantees.
- A holding stream ties up a chat pool slot for the length of the reply. Long replies under load mean more `llm_busy` errors.
- Each chat turn costs one or more LLM calls on the chat model. Read heavy questions can use six tool steps.
- Retention is lazy. A user who never opens chat again keeps idle threads until the next app restart.

**Neutral**:
- Two new browser libraries pinned from cdnjs (`marked`, `DOMPurify`). Upgrades are deliberate version bumps.
- A new Alembic migration and a new JSON store file (`backend/database/chat_store.json`, kept out of source control).
- New prompt files under `backend/prompts/agents/chat_assistant/`. The app guide must be kept in step with `docs/workflow.md` by hand.

## Follow-up

- [ ] Add `has_permission` checks to the existing routes that change data without one (generate scenarios, generate test cases, execute, batch execute, scenario and test case edit and delete), so the UI and chat enforce the same rules. Enrolled as scope feature 7.
- [ ] If the app ever runs with more than one worker, move the turn lock and rate limiter to PostgreSQL advisory locks or Redis (same limit as the spec 0003 pools).
- [ ] Agent Skills and MCP servers for `marked` and `DOMPurify` were looked for and declined (only unverified community skills exist). Record this on the `Declined:` line of root `AGENTS.md` `## Agent skills` so it is not offered again.
- [ ] Keyboard and screen reader support was left out of this version on purpose (mouse only). Plan it as a follow up: labelled button, Esc to close, focus handling, `aria-live` replies, Enter to send.
- [ ] Consider a running summary of older turns if users report the assistant forgetting earlier parts of long threads.
