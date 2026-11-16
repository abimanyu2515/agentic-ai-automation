# 0005. Chat assistant: decision record

Build spec: [index.md](index.md).

## Context

> ⚠️ Premise note: the first version covers reading data, generating, running, and creating, editing, and deleting four record types, all from free text. That is about 15 ways for an LLM driven flow to change data. An LLM will sometimes misread which item the user means, and project text (requirements, page data, error messages) can steer it. The confirm card and the typed `DELETE` contain that risk, but they only work if the card shows exactly what will run. The build plan therefore proves one action end to end (slice 3) before adding the rest, and keeps the catalog explicit rather than letting the model call arbitrary routes.

> ⚠️ Premise note: many routes the assistant would act through have no permission check today (generate scenarios, generate test cases, execute, batch execute, record edits and deletes). "Use the same check as the matching route" would mean no check for most actions. The spec maps each action to an explicit seeded permission module instead (see Rationale), and lists fixing the routes as a follow up.

The app is a single page app (vanilla HTML, CSS, and JS) over a FastAPI backend, with features spread across many views: requirements, scenarios, test cases, the object repository, Playwright scripts, executions, and reports. Answering a simple question ("why did the login test fail yesterday?") means moving between three or four views. Routine actions (rerun a test, regenerate scenarios) each live on a different screen.

Forces that shaped the design:
- **Existing LLM layer**: LiteLLM behind `LLMService`, with role pools (spec 0003) that cap concurrency and handle 429s. The analysis and generation pools are already busy during workflow runs.
- **Existing patterns**: the repository abstraction (JSON default, PostgreSQL optional), `WorkflowState` agents for the pipeline, SSE streams for executions, `log_audit`, and a seeded RBAC (role based access control) module and action grid.
- **Single process**: the app runs as one uvicorn process. Role pools and job state are already in memory.
- **Testability**: `AGENTS.md` requires injected LLM and runner doubles; nothing may need a live provider.
- **Engineer's requirements**: actions included (with deletes), a confirm card on every action, per user per project saved threads, streaming, view awareness, help mode without a project, and 90 day idle retention without a scheduler.

## Options considered

### Option 1: Tool calling loop in a `ChatService` (chosen)

A plain loop in a service: send messages and tool schemas through `LLMService` on a new `chat` role, run read tools on the server, end the turn when the model calls a propose tool, and run confirmed actions through existing services.

**Pros**:
- Reuses role pools, the repository, and existing services. No new framework state.
- Easy to test: stub the LLM's chunks, fake the executor.
- The propose then confirm split is explicit in the data, not hidden in framework internals.

**Cons**:
- The loop, tool call reassembly from streamed chunks, and step limits are written by hand.
- `LLMService` needs a new streaming and tools method.

### Option 2: LangGraph ReAct agent with interrupt

A separate LangGraph graph with an agent node, a tools node, and `interrupt` before action tools, saved with a LangGraph checkpointer.

**Pros**:
- Matches the LangGraph stack. Loop, tool routing, and pause and resume come built in.
- Easy to grow into multi step plans later.

**Cons**:
- Two sources of truth for a conversation: the checkpointer state and the chat repository the engineer asked for (JSON and PostgreSQL).
- Streaming tokens through a graph and resuming on an HTTP confirm click adds moving parts and harder tests.
- Checkpointer storage has no JSON store equivalent, which breaks the default provider.

### Option 3: Fold chat into the workflow graph

Add a chat node to the existing `WorkflowState` graph.

**Pros**:
- One graph to operate.

**Cons**:
- Chat is not a pipeline stage. It would break the `WorkflowState` agent contract (agents own fields and run in order), and every chat turn would carry pipeline state.

### Sub decisions settled during the conversation (with the engineer's picks)

| Dimension | Picked | Runner up | Why |
|---|---|---|---|
| Core job | Answer plus take actions | Read only answers | Engineer wants the assistant to do work, not just report it |
| Approval | Confirm card every time | Confirm only risky ones | No action runs on an LLM's reading alone |
| Context | Project plus current view | Project only | "Why did this fail?" with no ID must work |
| History | Saved per user per project, multiple threads | One running thread | Topics stay apart; context stays small |
| Action storage | Inside the assistant message JSON | Separate Action record | Engineer chose fewer tables (see Rationale) |
| Retention | Auto delete after 90 idle days, on startup and list | Background task | No scheduler exists |
| LLM role | New `chat` role falling back to analysis | Reuse analysis | Users waiting in chat should not queue behind batch analysis |
| Transport | POST returning SSE, read with `fetch` | WebSocket | Keeps the normal auth header; same SSE format as executions |
| Rendering | `marked` plus `DOMPurify` | Small built in renderer | Real tables and code blocks, with sanitizing |
| Deletes | Allowed, list targets and type `DELETE` | Create and edit only | Engineer wants deletes; typed confirm makes accidents hard |
| Expiry | A new user message expires earlier cards | 15 minute timer | Engineer's pick; ties expiry to the conversation moving on |
| Failure | Keep the partial reply, mark it, Retry | Discard it | The user already read it |
| Accessibility | Mouse only for now | Keyboard and screen reader basics | Engineer's pick; logged as a follow up |

### Update 2026-11-12: the open item on list screens

The first version of the vocabulary assumed each screen has a detail or edit form whose item is "open". The build showed that Requirements, Scenarios, Test Cases, and Executions have none: they are lists with expandable rows, inline edit, a requirement dropdown, and (for executions) a triage modal. The engineer settled where `item_id` comes from on those screens:

| Screen | Picked | Runner up | Why |
|---|---|---|---|
| Scenarios, Test Cases | Last opened row (expanded or editing), else the dropdown's requirement | Last opened row only | "Generate more scenarios for this" works with nothing expanded, while "why was this rejected" still points at one row |
| Requirements | View only, no item | Last selected requirement | Nothing is visibly open there; an invisible context would surprise the user |
| Executions | Triage modal execution, and `#/execution/<id>` as an execution | Triage modal only | The execution route shows an execution, so mapping it to a test case was wrong |
| Showing the context | Read only line above the input (AC-27) | Line with a remove button | The user always sees what "this" means, for little extra state |

The requirement fallback adds a second allowed item type on Scenarios and Test Cases, so the server allow list becomes `(view, item_type)` pairs instead of one type per view.

A cross check on a second model (Sonnet) found 13 gaps in this update, all settled with the engineer's approval: open rows clear on a list re-render (simpler and truer to the screen than restoring them), explicit hooks instead of DOM watching, one entry per row covering both expanded and editing, every exit from edit and from the triage modal named, per view lists for the approval screens, the fallback validated against the loaded requirements, one `chat:context-changed` event for the context line, exact context line text, per tab state, and a pure module so the rules can be checked on their own.

## Rationale

Option 1 fits the forces best. The engineer wants conversations stored through the repository on both JSON and PostgreSQL. That rules out a design whose real state lives in a LangGraph checkpointer (Option 2), because the two would drift and the JSON default would not work. Option 3 breaks the agent contract in `AGENTS.md`. A hand written loop (with tool call reassembly, locking, and disconnect handling) is a few hundred lines, all of them testable with the stub pattern the project already uses.

A cross check on a second model (Sonnet) found 23 decision gaps and 12 soundness issues in the first draft. All were settled with the engineer's approval: untrusted view context, propose and history storage rules, per type result mapping, restart recovery, replace mode creating before deleting, fixed ID lists for bulk deletes, the stream cap, atomic JSON writes, and cards built from server data rather than model text.

A separate `chat` role was chosen over reusing analysis because spec 0003's analysis pool runs at a concurrency cap of 2 during workflow runs, and an interactive user cannot wait 180 seconds. The chat role gets its own pool and a short max wait (30 s), so it fails fast with a clear "busy" message instead.

The engineer chose to keep actions inside the assistant message rather than as their own record (my recommendation was a separate record). That works, with two conditions written into the spec: every status change goes through one compare and set repository method (so a double click cannot run an action twice), and action URLs carry the message ID (so no JSON index is needed). The cost is a JSON column rewrite per status change and no direct "all actions" query. That is acceptable at chat volumes, and it stays an option to split the record out later.

The engineer asked for "the same RBAC check as the matching route". Most matching routes have no check, so the intent (actions follow the permission grid) is kept by mapping each action to an explicit `(module, action)` from the seeded modules (`Requirements`, `Scenarios`, `TestCases`, `Playwright`, `Executions`). This makes chat stricter than parts of the UI until those routes are fixed. That is the safer direction to differ in.
