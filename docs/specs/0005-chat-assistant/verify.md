# Verify: Chat assistant · spec 0005 · updated 2026-11-12
_Steps derived from spec 0005 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._
_Slice 1 (one streamed answer end to end). Later slices append their steps here._

## UI / manual
- [x] Sign in, look at the login page first → no chat button on the login page; after sign in a round button shows at the bottom right of every view → AC-1
- [ ] Click the button → a panel of about 400 by 600 px opens above it; the page behind still scrolls and clicks → AC-1
- [x] Click the expand control → the panel widens (about 760 px); click again → back to 400 px → AC-1
- [x] Resize the window under 640 px wide → the panel fills the screen as a sheet; the expand control is hidden → AC-1
- [ ] With no project selected, open the panel → help mode is available; send a question and receive a guide-only reply without creating a saved project thread → AC-3
- [x] Select a project, send "What does this project cover?" → the reply streams in word by word, the input is disabled and a Stop button shows while it streams, Send comes back when it ends → AC-7
- [x] After that first message, open the thread list → a thread titled with the first 80 characters of the message (extra spaces collapsed) is at the top → AC-5, AC-6
- [x] Click "New chat" → the panel clears; the thread list does not gain a new entry until you send a message → AC-5
- [ ] Switch views (Dashboard, Requirements, Playwright) with the panel open → it stays open on the same conversation → AC-2
- [x] Reload the page (as a non admin, or reselect the project as an admin) → the panel reopens on the same thread, loaded from the server → AC-2
- [x] Set `localStorage['chat_thread_<project id>']` to a random UUID and reload → the panel shows the empty new chat state and the hint is removed → AC-2
- [ ] With more than 20 threads, scroll the thread list → "Load more" fetches the next page; with more than 50 messages in a thread, scroll to the top → older messages load and the view does not jump → AC-6
- [ ] On the Playwright screen with a test case open, send a message → the request body's `view_context` is `{view: "playwright", item_type: "test_case", item_id: <that id>}` (check the network tab) → AC-4
- [x] Ask the model (or a stub) to reply with `**bold**`, a link, `<img src=x onerror=alert(1)>`, and `<script>alert(2)</script>` → bold renders, the link opens in a new tab with `rel="noopener noreferrer"`, no image appears, nothing runs, no alert → AC-23
- [x] Type `<b>hi</b>` as a user message → it shows as literal text, not bold → AC-23

## Commands
- [ ] `pytest -q backend/tests/test_chat_repository.py` → 14 pass (7 tests on the JSON store and 7 on the SQL store) → AC-25
- [ ] `pytest -q backend/tests/test_chat_service.py` → 26 pass, no live LLM call → AC-4, AC-5, AC-7, AC-9, AC-17 (partial), AC-21, AC-26
- [ ] `pytest -q backend/tests/test_llm_roles.py` → all pass, including the chat role fallback tests → AC-24
- [ ] `alembic current` → `d5e1a7c3b210 (head)`; `chat_threads` and `chat_messages` exist with cascade foreign keys → AC-25
- [x] `curl -H "Authorization: Bearer <token>" /api/v1/settings` → `llm_roles.chat` present without `api_key` or `api_base`, and `chat_enabled: true` → AC-24
- [x] As user B, `GET /api/v1/projects/<id>/chat/threads/<user A thread>/messages` → `404`; on a project B has no access to → `403` → AC-21
- [x] `POST .../chat/messages` with `{"content": "   "}` → `422`; with 4001 characters → `422` → AC-5
- [ ] Set `CHAT_ENABLED=false`, restart → chat routes return `404`, `chat_enabled` is false, no button renders → AC-1, AC-24

## Value sourcing (exercise the edge that breaks if the source is wrong)
- [x] Project ID comes from the path: send to project A with a `thread_id` from project B (same user) → `404` → Value sourcing: project ID
- [x] User ID comes from the token: two users on one project each see only their own threads → Value sourcing: user ID
- [ ] View and item come from `getChatContext()`, and the server drops foreign items: send a `view_context` whose `item_id` is a test case from another project → the system prompt shows the view only, no item → Value sourcing: current view and item
- [x] Thread title is derived from the first message: send "  a   b  " → title "a b" → Value sourcing: thread title
- [ ] History window: set `CHAT_HISTORY_MESSAGES=3`, have a 6 message thread → the LLM receives only the newest 3 rows, never a tool row without its call → Value sourcing: messages sent to the LLM
- [ ] App guide comes from `app_guide.md`: set `CHAT_GUIDE_CHARS=500` → an error is logged at the first chat turn and the guide is cut to 500 characters → Value sourcing: app guide text
- [ ] System prompt is filled with project name, today (UTC), and view context: check a captured prompt near midnight UTC shows the UTC date → Value sourcing: system prompt
- [x] Thread list order follows `last_activity_at`: post to an older thread → it moves to the top → Value sourcing: thread list order
- [ ] Settings view chat values come from `settings.llm_roles["chat"]`: set only `CHAT_LLM_MODEL` → the chat role shows that model with the analysis provider → Value sourcing: chat role values

## Open item rules and context line (added 2026-11-12, closes the earlier AC-4 gap)
_Browser behaviors: check the `view_context` in the network tab and the "About:" line above the input._
- [ ] On Test Cases, expand test case A, then B → line reads "About: test case '<B>'" and `view_context` is `{view: "testcases", item_type: "test_case", item_id: <B>}` → AC-4, AC-27
- [ ] Collapse B → "About: test case '<A>'"; collapse A → "About: requirement '<selected requirement>'" → AC-4, AC-27
- [ ] Switch project → no item; the line names the view only → AC-4
- [ ] On Scenarios, expand a scenario, click Edit, collapse the row → still "About: scenario '<name>'"; click Cancel → falls back to the requirement → AC-4
- [ ] Put a scenario in edit, clear its name so the save fails → it stays the item; a successful save re-renders the list → falls back to the requirement → AC-4
- [ ] Expand a test case row, then approve a different test case (this triggers `refreshProjectData`) → nothing open; the line shows the requirement → AC-4
- [ ] Change the requirement dropdown on Scenarios → open rows clear; the line shows the new requirement → AC-4
- [ ] On Executions, open a triage modal → the modal is visible and the line reads "About: execution '<test case title>'"; close with ✕, with Close, or by changing the route → the line names the view only → AC-4, AC-27
- [ ] Open `#/execution/<id>` → `view_context` is `{view: "execution-workspace", item_type: "execution", item_id: <id>}` → AC-4
- [ ] On Object Repository, select a page → "About: page '<page name>'" → AC-4, AC-27
- [ ] On Requirements, Dashboard, or Reports → "About: <sidebar label>" with no item → AC-27
- [ ] Give a scenario a title containing `<b>` and 100 characters, open it → the line shows literal `<b>`, cut to 60 characters with "…"; the name never appears in the request body → AC-27
- [ ] With no project selected → the line is hidden → AC-27
- [ ] On Test Data Agent → no item is sent (the screen has no single open test case) → AC-4
- [ ] `pytest -q backend/tests/test_chat_service.py` → 26 pass, including the `(view, item_type)` allow list: `(testcases, execution)` dropped, `(requirements, requirement)` dropped, `(scenarios, requirement)` kept only for a requirement in this project → AC-4

## Acceptance-criteria coverage (slice 1)
- AC-1: UI steps 1 to 4, Commands last step · AC-2: UI steps 9 to 11 · AC-4: UI step 13, Value sourcing step 3, open item steps 1 to 10, 14, 15 · AC-5: UI steps 6 and 7, Commands step 7 · AC-6: UI steps 6 and 12 · AC-7: UI step 6 · AC-9: Value sourcing step 5 · AC-21: Commands step 6, Value sourcing steps 1 and 2 · AC-23: UI steps 14 and 15 · AC-24: Commands steps 3, 5, 8 · AC-25: Commands steps 1 and 4 · AC-27: open item steps 1, 2, 8, 10 to 13

# Verify: chat assistant, slice 2 (read tools) · spec 0005 · updated 2026-11-12
_Steps derived from AC-8 and AC-9. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [ ] Open a failed test case on Test Cases, ask "why did this fail?" → the bubble shows "Looking up a test case…" or "Looking up executions…" while the tool runs, then an answer naming the real error message and failed step from that execution → AC-8, AC-4
- [x] Ask "how many requirements and test cases does this project have?" → the counts match the Dashboard → AC-8
- [ ] Ask "show my dashboard metrics" → pass ratio and totals match `GET /projects/{id}/dashboard-metrics` → AC-8
- [ ] Ask about a test case whose test data has a `password` key → the answer never shows the password value (the tool sends `[redacted]`) → AC-8
- [ ] Reload and reopen the thread → only user and assistant bubbles appear; no tool rows or tool call text → AC-6, AC-8
- [ ] Ask a follow up in the same thread ("and the one before that?") → the answer uses the earlier lookup without repeating it, showing the replayed history kept each tool call with its result → AC-9

## Commands
- [ ] `pytest -q backend/tests/test_chat_tools.py backend/tests/test_chat_service.py backend/tests/test_chat_repository.py` → 66 pass → AC-8, AC-9, AC-26
- [x] Set `CHAT_MAX_TOOL_STEPS=1`, restart, ask a question needing two lookups → one `tool` start/end pair in the stream, then an answer ending "This answer may be incomplete: the lookup limit for one message was reached." → AC-8
- [ ] Set `CHAT_TOOL_RESULT_CHARS=500`, ask to list all test cases → the saved `tool` row in `backend/database/chat_store.json` has a `tool_result` of at most 500 characters that parses as JSON with `"truncated": true` and `"total"` → AC-8
- [ ] Ask about a test case ID from another project (paste it) → the tool row's result is `{"error": "not found in this project"}` and the answer says it was not found → AC-8, invariants

## Value sourcing
- [ ] Messages sent to the LLM: with `CHAT_HISTORY_MESSAGES=3`, after a turn that called a tool, the next turn's request never holds a `tool` message without the assistant `tool_calls` message before it (`test_history_keeps_newest_within_char_cap_and_tool_units_whole`, `test_replayed_history_puts_tool_results_before_the_answer`) → AC-9

## Acceptance-criteria coverage (slice 2)
- AC-8: UI steps 1 to 5, Commands steps 1 to 4 · AC-9: UI step 6, Commands step 1, Value sourcing step 1

# Verify: chat assistant, slice 3 (one action: generate_scenarios) · spec 0005 · updated 2026-11-12
_Steps derived from AC-10 to AC-12, AC-14 to AC-16, and AC-22. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [x] On Scenarios with a requirement selected, ask "generate 2 more scenarios for this requirement" → a card appears under the reply: "Generate 2 scenarios for requirement '<its title>'", status "Waiting for you", Run and Cancel. No new scenario exists yet → AC-10
- [x] Click Run → the card shows "Running…", then "Done" with "Created 2 scenarios" and an "Open scenarios" link; the Scenarios list behind the panel reloads and shows the 2 new rows without a page refresh → AC-12, AC-15, AC-16
- [ ] While a card is running, send another question → the answer streams while the card keeps updating → AC-15
- [ ] Start a run from the Dashboard view, then click "Open scenarios" when it finishes → the Scenarios screen opens on that requirement with the new rows → AC-15, AC-16
- [ ] Reload the page while a card is "Running…" → the panel reopens on the thread and the card keeps polling until "Done" → AC-15
- [x] Ask for a card, then click Cancel → status "Cancelled", no Run button, no scenarios created → AC-12
- [x] Ask for a card, then send any other message → the earlier card turns "Expired, ask again" with no buttons → AC-14
- [ ] Ask to "replace the scenarios of this requirement with 3 new ones" → the card lists every current scenario (name and short ID) under "Then delete…", Run stays disabled until you type exactly `DELETE`; after Run the old scenarios are gone and 3 new ones exist → AC-10, AC-13 (replace path)
- [ ] Open the same card in a second tab and click Run in both → one runs; the other shows the real status (no second run) → AC-12
- [x] As a user whose role lacks Scenarios create on this project, ask to generate scenarios → no card; the assistant explains which screen and button does it → AC-11
- [x] Get a card as a permitted user, remove the Scenarios create permission from the role, click Run → "You no longer have permission", status stays "Waiting for you" → AC-11

## Commands
- [ ] `pytest -q backend/tests/test_chat_actions.py backend/tests/test_chat_service.py backend/tests/test_chat_repository.py backend/tests/test_chat_tools.py` → 90 pass → AC-10 to AC-12, AC-14, AC-15, AC-22, AC-25, AC-26
- [x] `curl -X POST .../chat/messages/<id>/action/confirm -d '{"params":{"count":10}}'` on a card proposed with count 2 → 2 scenarios are created, not 10 → AC-12
- [x] Confirm the same card twice with curl → first `200`, second `409` → AC-12
- [ ] Confirm a replace card with `{"confirm_text":"delete"}` → `422`; with `"DELETE"` → `200` → AC-13 (replace path)
- [ ] Delete the requirement after the card is proposed, then confirm → `422` "This item changed or no longer exists, ask again" → confirm rules
- [x] Query `audit_logs` after a run and a cancel → rows `CHAT_ACTION_CONFIRMED`, `CHAT_ACTION_SUCCEEDED`, `CHAT_ACTION_CANCELLED` with `field_changes.source = "chat"`, the action type, target IDs, and outcome → AC-22
- [ ] Make generation fail (bad chat or analysis key) on a replace card → status "Failed" with the error, and the old scenarios are still there → AC-15 (replace safety)
- [ ] Confirm a card, stop uvicorn before it finishes, start it again → the card shows "Failed" with "Interrupted by a server restart" → AC-15 (startup recovery)
- [ ] With `REPOSITORY_PROVIDER=postgres`, repeat the Run and double confirm steps → same results → AC-25

## Value sourcing
- [ ] Confirm parameters: run the saved `action.params`, never the request body (curl step 2) → AC-12
- [ ] Permission decision: decided by `check_permission` at propose time (which tools are offered) and again at confirm (UI steps 10 and 11), per project role first, then the global role → AC-11
- [ ] Targets and delete list: names and the replaced scenario list come from the repository at propose time; a scenario deleted between propose and confirm makes confirm fail with `422` instead of deleting a different set → AC-10, AC-13
- [ ] Which view reloads: `action.affects` (`["scenarios"]`); on Requirements the list does not reload, on Scenarios it does → AC-16

## Acceptance-criteria coverage (slice 3)
- AC-10: UI steps 1 and 8, Value sourcing step 3 · AC-11: UI steps 10 and 11, Value sourcing step 2 · AC-12: UI steps 2, 6, 9, Commands steps 2 and 3 · AC-13 (replace path only; delete actions arrive in slice 4): UI step 8, Commands step 4 · AC-14: UI step 7 · AC-15: UI steps 2 to 5, Commands steps 7 and 8 · AC-16: UI steps 2 and 4, Value sourcing step 4 · AC-22: Commands step 6

# Verify: completed chat assistant slices 4–7 · spec 0005
_Automated checks for the remaining action catalog, lifecycle, housekeeping, documentation, and batch execution._

## Commands
- [x] `pytest -q backend/tests/test_chat_repository.py backend/tests/test_chat_service.py backend/tests/test_chat_actions.py backend/tests/test_chat_tools.py backend/tests/test_execution_service.py backend/tests/test_api.py::test_batch_execution_endpoints backend/tests/test_object_repository.py::test_batch_route_attributes_each_harvest_file_to_its_own_test_case` → 102 passed; uses LLM, repository, runner, and queue fakes → AC-3, AC-11 to AC-20, AC-22, AC-26
- [x] `docs/api.md` documents the chat help, retry, deletion, action catalog, and streaming routes → AC-24

## Slice 4: the rest of the action catalog · updated 2026-11-12
_Steps for the actions beyond `generate_scenarios`. Run the UI steps against the real app with a project that has scripted test cases, and the commands against a running backend._

### UI / manual
- [ ] Open a test case and ask "delete this test case" → the card says "Will delete:" with the test case name and short ID; Run stays disabled until you type exactly `DELETE` (not `delete`, not `DELETE `); after Run the card shows "Done" and the Test Cases list behind it reloads without the row → AC-13, AC-16
- [ ] Ask "delete requirement '<name>'" → the card lists the requirement and a count row such as "3 scenarios and 12 test cases" with no ID chip; after typing `DELETE` and Run, the requirement, its scenarios, and its test cases are gone from every screen → AC-13
- [ ] With a mix of passed, failed, and error runs, ask "clean up the failed executions" → the card lists the failed and error runs (up to 20, then "and N more"), each named by its test case title, or the first 8 characters of its ID when the test case is gone; passed runs are not listed → AC-13
- [ ] On that card, let another run fail, then type `DELETE` and Run → the summary reads "Deleted N executions" for the listed runs only, and the run that failed afterwards is still there → AC-13 (only the proposed IDs)
- [ ] Ask "run this test case" on a scripted test case → the card goes "Queued", then "Running…", then "Done" with "Passed in N s" or "Failed: <error>", and an "Open executions" link; a failing test is still "Done", because the run itself worked → AC-15
- [ ] Ask "run these three test cases" → the card goes "Running…", then "Done" with "N passed, M failed, K skipped"; the Executions view reloads → AC-15, AC-16
- [ ] Ask "generate a script for this test case" → the card goes "Queued", then "Running…" while the script is recorded, then "Done" with "Script generated" and "Open the script" → AC-15
- [ ] Ask "set the priority of this requirement to high and its domain to payments" → the card shows "Will set:" with each field as old value → new value; after Run, reload the Requirements screen and both new values are kept → AC-11, edit cards
- [ ] Ask to rename a scenario, then rename it yourself in the UI before clicking Run → Run sets the proposed name ("will set", not a merge) → confirm rules
- [ ] Remove a role's `Executions/create` permission, then ask to run a test case → no run card is offered; with the permission removed after the card appears, Run shows "You no longer have permission" → AC-11

### Commands
- [ ] Confirm a `delete_test_case` card with no body, then `{"confirm_text":"delete"}` → `422` both times and the test case still exists; then `{"confirm_text":"DELETE"}` → `200` and an audit entry with `source: "chat"` → AC-13, AC-22
- [ ] Start a run card, restart uvicorn before the run saves its result, then poll `GET .../action` → `failed` with "Interrupted by a server restart"; a run that saved its result before the restart shows its verdict instead → AC-15 (startup recovery)
- [ ] `pytest -q backend/tests/test_chat_action_catalog.py backend/tests/test_chat_actions.py backend/tests/test_execution_service.py` → all pass on fakes, no browser or provider → AC-11, AC-13, AC-15, AC-26

### Value sourcing
- [ ] Confirm reads only the saved `params`: send `{"confirm_text":"DELETE","params":{"test_case_id":"<another id>"}}` on a delete card → the card's own test case is deleted, the other one is untouched → AC-12
- [ ] Status comes from the job: while a run card polls, check that the execution record and `active_execution_events` match what the card shows; rerunning the same test case never shows the earlier run's "Completed" as this run's result → AC-15

### Acceptance-criteria coverage (slice 4)
- AC-11: UI steps 8 and 10, Commands step 3 · AC-12: Value sourcing step 1 · AC-13: UI steps 1 to 4, Commands step 1 · AC-15: UI steps 5 to 7, Commands step 2, Value sourcing step 2 · AC-16: UI steps 1 and 6 · AC-22: Commands step 1
