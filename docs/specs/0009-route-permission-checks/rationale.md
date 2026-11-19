# 0009. Rationale: route permission checks

## Context

The app has a working RBAC model (role based access control: roles hold module and action grants, and a project role can override the global role). Spec 0005 pulled it into `check_permission` and `require_project_access`, and the chat assistant checks both before every action. Most UI routes never adopted it. A scan of `backend/main.py` on 2026-11-18 found:

- 27 data changing routes with no login check at all.
- 5 that check the login but no permission (Jira import, Jira sync, bug sync, script jobs, and demo reset with a hardcoded role list).
- Object repository and project user routes that check permission but not project membership. Because `has_permission` falls back to the global role, a Tester on one project can change another.
- Two machine callers with no proof of identity: the Playwright workspace posting run results, and Jira posting issue events.
- `GET /projects/{id}/credentials`, open to anyone, returning decrypted credentials and falling back to another project's.

The workspace page on port 3000 also calls `PUT /playwright/freeze` and `/playwright/script` straight from the browser with no token, so any fix to those routes has to keep the workspace working. The workspace is started on its own with `pnpm dev` and runs on the same machine as the backend.

Not deciding leaves the UI and chat enforcing different rules, lets anyone who can reach the server fake run results, and keeps project passwords exposed.

## Options considered

### Option 1: Fix in place with shared dependencies

Add the existing helpers to each route, plus two secret dependencies and one helper for routes whose project comes from the record. A route audit test catches new routes.

**Pros**: small, reviewable diffs per route; reuses the exact checks the chat uses; no structural change to `main.py`; can ship slice by slice.
**Cons**: each new route still has to opt in (the audit test catches a miss, but only in CI); many similar edits.

### Option 2: Auth by default on a router

Move routes onto an `APIRouter` with `get_current_user` as a router dependency, and opt out the login and webhook routes.

**Pros**: a new route is protected without anyone remembering; strongest default.
**Cons**: moves 100+ routes out of `main.py` in one change, a big bang refactor of a busy file; still needs per route permission and ownership checks, so most of Option 1's work remains.

### Option 3: Middleware permission table

A middleware maps method and path patterns to module and action, and checks before routing.

**Pros**: one table to read; covers routes without touching them.
**Cons**: path patterns drift from real routes silently; can't do ownership checks (needs the repository and parsed IDs); a second place where auth lives, beside the dependencies the rest of the app uses.

### Webhook identity (sub decision)

- **Shared secret per webhook (chosen)**: works whatever the network setup; simple to rotate; Jira can carry it in the URL or a header.
- **Localhost only for the workspace**: no workspace change, but breaks the day the workspace runs in another container.
- **Leave open**: run results and Jira reindexing stay forgeable.

### Workspace script editing (sub decision)

- **Proxy through the workspace server with the secret (chosen)**: small change; keeps editing in the workspace. Its cost is that whoever can open port 3000 can edit scripts.
- **Hand the user's login to the workspace**: correct per user permissions, but needs a token handoff endpoint and a new workspace start flow.
- **Move editing to the main app**: cleanest permissions, but loses editing in the workspace.

## Rationale

The forces are: the checks already exist and are trusted by the chat, so the gap is adoption, not design; `main.py` is large and actively changing, so a structural move risks merge pain across every open branch; and ownership (AC-4) needs the repository, which only dependencies or route code can reach. Option 1 fixes the actual gap with the least risk, and the audit test covers its one weakness (forgetting) at the point where it matters. Option 2 can follow later if the audit test keeps catching misses.

The engineer chose to cover every data changing route rather than the six feature 7 named, because the work per route is the same and a partial fix leaves the same hole open. The credentials route was pulled in despite being a read route, because it leaks decrypted secrets across projects and the workspace secret already covers its only legitimate caller.

Reused permissions follow the chat catalog where a chat action exists (generate test data uses `TestCases.edit` there, so the route does too). Releases and cycles reuse `Executions.create`: Testers and Project Managers already hold it, Stakeholders don't, and it needs no seed migration (runner up: a new `Releases` module). Global routes that rewrite `.env` or wipe data use `Settings.edit`, the same as `PUT /settings`.

Missing secrets fail closed, because an open default is easy to ship by mistake. The access log filter exists because uvicorn logs full query strings, and the Jira secret may travel there.

The toast is limited to 403 and to the buttons this spec touches. The app's 52 `alert()` catch blocks stay as they are, so the change stays scoped (runner up: replace every alert, which is a separate cleanup).
