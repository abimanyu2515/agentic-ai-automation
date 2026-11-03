# Object Repository

Spec: [0004](../specs/0004-object-repository/index.md). API: [api.md](../api.md#object-repository).

## What it does

Each project keeps a list of its pages and the UI elements on them. Every element has one main locator (a rule for finding it on the page) and up to four backups. Generated scripts refer to elements by key, such as `login_page.login_button`, and the backend inserts the current locators right before a script is read or run. Fixing one locator fixes every script that uses it, with no regeneration.

```js
const btn = await orx(page, 'login_page.login_button');
await btn.click();
```

## Where the data comes from

* **Reconnaissance** fills it automatically for projects marked as a safe test environment: during script generation, grounded validation, or **Scan now**. Pages are matched by URL pattern. An unmatched path creates a page whose pattern replaces numeric and UUID segments with `*` (`/booking/12345/details` becomes `/booking/*/details`).
* **Test runs** harvest the pages they visit, on every project and on both runners (the workspace engine and the Python runner). A run already logs in and follows the real flow, so it reaches pages behind login that a scan can't. Each distinct path the page settles on (1000 ms with no navigation) is snapshotted once per execution, for at most `harvest_max_pages` paths. A harvest only adds and updates elements: it never marks one `missing` and never changes `last_scanned_at`. Failed runs are harvested too, and harvesting never changes a run's result.
* **Testers** add and fix pages and elements in the **Object Repository** view, or through the API. A tester edit marks the element `manually_edited`, and neither reconnaissance nor a harvest changes it again.

Reconnaissance and both runners use one extractor, `backend/playwrightt/lib/object-harvest.js`, so a scan and a run see the same element the same way and it ends up as one element.

**What a harvest never stores**: the extractor never reads what was typed into a field. The runner replaces the target username and password with `[redacted]`, and the backend redacts again with the project's vault credentials and every test data value of the test case (case insensitive, values shorter than 3 characters ignored). A locator whose value was redacted is dropped, values that look like tokens (32 or more hex or base64 characters) are dropped, and an element left with no locator is dropped. Snapshot URLs keep the path only.

On a rescan, reconnaissance matches elements by fingerprint (role, type, accessible name, and the first of `data-testid`, `id`, `name`). It replaces the locators of elements it owns, adds new ones, marks unseen ones `missing`, restores a `missing` element it sees again, and never recreates a deleted element. Elements with no accessible name and no stable attribute get no fingerprint, so a later scan adds them again.

## Health

| Health | Set when |
|---|---|
| `healthy` | a tester saves the element, a run used the main locator every time, or reconnaissance sees a `missing` element again |
| `needs_review` | a run needed a backup locator, the first run in a row where every locator failed, or every locator reconnaissance built matches more than one element |
| `broken` | the second run in a row where every locator failed |
| `missing` | reconnaissance scanned the page and did not see a recon owned element |

Each lookup at run time logs `[OBJREPO] {"key", "index", "tried", "ok"}`. One execution updates each element once, by its worst outcome, and the same execution is never applied twice. `broken` elements are left out of the generator's prompt.

## Configuration

`backend/config/default_config.yaml`:

```yaml
object_repository:
  max_prompt_elements: 150      # cap on elements listed in one prompt
  locator_timeout_ms: 3000      # wait per locator before trying the next
  scan_status_ttl_seconds: 3600 # how long a finished scan's status stays readable
  harvest_enabled: true         # global switch for harvesting during test runs
  harvest_max_pages: 20         # distinct paths harvested per execution
  harvest_page_budget_ms: 5000  # deadline per snapshot, and the end of test wait
```

The workspace engine reads the harvest settings at run start from `GET /api/v1/object-repository/harvest-settings` (any failure means off for that run). The Python runner passes them to the test process as `OBJREPO_HARVEST`, `OBJREPO_HARVEST_MAX_PAGES`, and `OBJREPO_HARVEST_BUDGET_MS`.

## Permissions

Module `ObjectRepository`: Admin gets every action, Project Manager gets view, create, edit, and delete, Tester gets view, create, and edit, and Stakeholder gets view. Project scoped roles apply. The view reads the caller's actions from `GET .../object-repository/permissions`, so it shows only the controls that will work.

## Good to know

* Stored scripts can't run on their own outside this system. Copy a script from an API response (it's rendered), not from the database.
* A rename rewrites every stored script that quotes the old key, frozen and approved ones included. It's audited but can't be undone.
* Keys must be plain quoted strings. A key built at run time (a template string or a variable) is reported as unknown.
* Selenium scripts get stored locators through `orx(driver, key)`, but no health feedback yet.
* Scan status lives in memory per process. A restart loses an in flight scan's status (the data it already saved stays).
* An element whose locators all fail costs up to `5 × locator_timeout_ms` before the step errors.
* Harvesting runs on every project, not only safe test environments, and `harvest_enabled` is global. A run's first visit to a page costs up to `harvest_page_budget_ms`.
* A page the test leaves or ends on within 1000 ms of arriving never settles, so it isn't harvested.
* A run sees pages mid flow, so it can add controls that exist only in that state (an open dialog's button). Runs never mark elements missing and scans can't reach pages behind login, so delete those by hand.
* Hash routed apps (`#/cart`) count as one path, so only their first settled view is harvested.
* Redaction is by exact value. A credential or test data value the page shows transformed (split across elements, partly masked) isn't caught. Different letter case is caught.

## Code map

| Area | File |
|---|---|
| Models | `backend/models/object_repository.py` |
| Stores (JSON, PostgreSQL) | `backend/repository/object_repository_store.py`, `backend/repository/postgres_object_repository_store.py` |
| Business rules | `backend/services/object_repository_service.py` |
| Render and strip | `backend/services/object_repository_script.py` |
| Scan job | `backend/services/object_repository_scan.py` |
| Shared extractor | `backend/playwrightt/lib/object-harvest.js` |
| Reconnaissance extraction | `backend/agents/application_reconnaissance_agent.py` (`_extract_object_elements`) |
| Run markers | `ExecutionService.apply_object_repository_markers`, `backend/playwrightt/lib/execution-engine.ts` (`logs` field) |
| Harvest timing (both runners) | `backend/playwrightt/lib/object-harvest-session.ts` |
| Harvest transports | `execution-engine.ts` (`harvest` field), `backend/services/playwright_runner.py` (`harvest.json`) |
| Harvest cleaning and ingest | `backend/services/object_repository_harvest.py`, `ExecutionService.apply_object_repository_harvest` |
| View | `frontend/js/object-repository.js` |
| Migration | `backend/database/alembic/versions/c3f8a1d2e905_add_object_repository.py` |
| Tests | `backend/tests/test_object_repository.py`, `backend/tests/test_object_harvest_browser.py`, `backend/e2e/object-repository.spec.ts`, `backend/e2e/object-harvest-session.spec.ts` |
