# 0004. Store project UI elements in an object repository that scripts reference by name

**Date**: 2026-11-03
**Status**: In Progress

## Summary

Each project gets an object repository: a stored list of its pages and the UI elements on them, each with one main locator (a rule for finding the element on the page) and up to four backups. Reconnaissance fills it automatically and testers can fix it by hand. Generated scripts refer to elements by name, and the backend inserts the current locators right before every run, so fixing one locator fixes every script that uses it. Runs report which locator worked, so testers can see which elements are going stale.

Test runs also harvest elements (collect them while the run is on the page). A run already logs in and walks the real flow, so it reaches the pages a logged out scan never can. A run only adds and updates elements, never marks them missing, takes one snapshot per page, and strips credentials and test data before anything is saved. (Update, 2026-11-03.)

## Requirements

**User stories**:
- As a tester, I want generated scripts to use stored, named elements so that runs stop failing because the model invented a selector.
- As a tester, I want to fix a broken locator once so that every script using that element picks up the fix without being generated again.
- As a tester, I want to see which elements needed a backup locator or failed so that I can repair them before they break more runs.
- As a project manager, I want to control who can change shared locators so that one bad edit doesn't break the whole suite.
- As a tester, I want the pages my test runs visit after login to fill the repository so that I don't have to enter those elements by hand.

**Acceptance criteria**:
- **AC-1**: When reconnaissance inspects a project marked `is_safe_test_environment` (during script generation or a scan), every visible button, link, form field, and navigation link (an anchor inside `nav` or `[role=navigation]`) on each inspected page is saved under the page whose `url_pattern` matches the page path. If no page matches, a page is created whose pattern is the path with numeric and UUID segments replaced by `*`. Each saved element has a snake_case name unique on its page, an `element_type`, a fingerprint (empty when the element has no accessible name and no stable attribute), and 1 to 5 ordered typed locators.
- **AC-2**: When reconnaissance sees a page again, it matches elements by fingerprint. It replaces the locators of elements it owns (`manually_edited = false`), never changes an element with `manually_edited = true`, adds new elements, sets elements it owns but didn't see on that page to `missing`, sets a `missing` element it sees again back to `healthy`, and never recreates an element whose fingerprint belongs to a soft deleted element on that page.
- **AC-3**: `POST .../object-repository/scan` returns 202 with a `scan_id`. `GET .../object-repository/scan/{scan_id}` reports `queued`, `running`, `succeeded`, or `failed`, with pages scanned and elements added, updated, and marked missing. The scan returns 409 when the project is not a safe test environment or a scan is already running for the project.
- **AC-4**: Testers can create, update, and soft delete pages and elements through the API. Invalid input returns 422: a name that isn't snake_case, a bad glob (empty, no leading `/`, containing `***` or `?`), 0 or more than 5 locators, an unknown strategy or element type, or an empty locator value. A duplicate page name, page pattern, or element name returns 409. Any tester create or update sets `manually_edited = true`, `health = healthy`, and `failure_count = 0`. Locator rows whose `(strategy, value, options)` didn't change keep their success and failure counters.
- **AC-5**: Renaming a page or element rewrites every exact reference to each affected key (quoted with `'`, `"`, or a backtick) in the project's stored test case scripts, including frozen and approved ones, in the same transaction. `scripts_rewritten` counts test cases. Deleting an element returns the number of test cases whose scripts still reference it.
- **AC-6**: When generating a Playwright or Selenium script, the generator gets the elements of every page whose pattern matches a URL the test case will visit. Soft deleted and `broken` elements are left out, `healthy` elements come before `needs_review` and `missing`, and the list is capped by `object_repository.max_prompt_elements` (default 150), with a warning logged when the cap cuts the list. The prompt tells the model to locate listed elements only through `orx(page, '<page>.<element>')` (`orx(driver, ...)` for Selenium) and to use the existing DOM context for anything not listed. A test case whose pages are all unmapped is generated exactly as today.
- **AC-7**: If a generated script references an element key that isn't in the repository, the existing `repair_feedback` loop (the Playwright paths in `workflow_service` and `grounded_playwright_service`) runs once and names the unknown keys. If the keys are still unknown, the test case's validation diagnostic is set to `Unknown object repository keys: <keys>`. Selenium and multi dataset generation check for unknown keys and set the same diagnostic without a repair. Keys built at run time (template strings) are not supported and are reported as unknown.
- **AC-8**: Stored scripts hold element keys only, never the injected block. Strip happens only in `project_service`'s test case script update (every save goes through it). Render happens in one `TestCase` response mapper used by every script response in `backend/main.py`, in the `/api/v1/playwright/workspace` responses, and in `ExecutionAgent` when it builds each `QueueItem`. Render always resolves the project from the test case (scenario → requirement → project), never from a query parameter. Changing a locator therefore changes the next run of every script that uses it, with no regeneration.
- **AC-9**: At run time `orx` tries an element's locators in order and waits up to `object_repository.locator_timeout_ms` (default 3000) for each to become visible. It returns the first locator that becomes visible and logs one `[OBJREPO]` marker with the key, the winning `index`, and `tried` (how many locators it attempted). If every locator fails, or the key is unknown, it logs a failure marker and throws an error naming the key. Locator values are written into the block as JSON with `(` escaped as `(`, so page text can't trip the engine's danger check.
- **AC-10**: Only `ExecutionService.finalize_execution` applies markers. The workspace path passes the `completed` payload's `logs`, and the Python path passes the lines it collected through `on_log_callback`. Markers are grouped per key for each execution, and each element is updated once per execution by the worst outcome. A run where index 0 always won sets `healthy` and resets `consecutive_failed_runs`. A run where a backup won sets `needs_review`. A run where every locator failed adds 1 to `failure_count` and `consecutive_failed_runs`, and sets `broken` when `consecutive_failed_runs` reaches 2 (otherwise `needs_review`). Per execution, the winning locator gets `success_count + 1` and `last_succeeded_at`, and locators 0 to `index - 1` (all `tried` locators on a total failure) get `failure_count + 1`. The element gets `last_used_at`, `last_execution_id`, and `last_locator_index` from the last marker. Malformed or unknown markers are skipped with a warning and never fail the webhook. An execution ID already in the element's `applied_execution_ids` changes nothing.
- **AC-11**: Every object repository route requires the `ObjectRepository` permission (`view`, `create`, `edit`, or `delete`, as listed in the API table) and returns 403 otherwise. The sidebar entry is hidden without `view`.
- **AC-12**: Tester creates, updates, and deletes write one audit entry each through `AuditService`, with the user, the entity, and a before and after summary of its locators. Each scan writes one summary audit entry with its counts.
- **AC-13**: The Object Repository view lists the selected project's pages with health counts, shows a page's elements (name, type, health badge, main locator, last used), filters elements by health, edits an element's locators in a drawer (add, remove, reorder, at most 5), starts a scan and shows its progress, and shows empty, loading, and error states. It is fully usable with a keyboard, and badges show health as text, not colour alone.
- **AC-14**: Everything above behaves the same on the JSON store and on PostgreSQL. After any SQLAlchemy error, the repository rolls back and clears the session before the next operation.
- **AC-15**: When reconnaissance and a tester write the same element at the same time, the tester's edit wins. Reconnaissance locks the element row (`SELECT ... FOR UPDATE` in PostgreSQL, the existing file lock in the JSON store) and checks `manually_edited` again before writing.

**Harvest during execution (update 2026-11-03)**:
- **AC-16**: During every Playwright run on either runner (the workspace engine and the Python `PlaywrightRunner`), for every project, when `object_repository.harvest_enabled` is true (default true): each distinct URL path the run's main page settles on is snapshotted at most once per execution, for at most `object_repository.harvest_max_pages` paths (default 20), and at most 300 elements per snapshot. The backend enforces the once per path rule and the page cap per execution, whatever the runner sends. A snapshot collects the same element kinds, attributes, built locators, and match counts as AC-1, using the shared extractor. A page settles when 1000 ms pass with no main frame navigation (client side route changes count as navigations). Popups and other tabs are not harvested. Paths are normalized like page matching (trailing slash removed, case kept); hash routes (`#/x`) all count as their one path.
- **AC-17**: Harvesting never changes a run's outcome. Extractor errors, page closes, and transport failures are skipped with a warning. Each snapshot is abandoned after `object_repository.harvest_page_budget_ms` (default 5000), and the run waits at most that budget once more at the end of the test for a pending snapshot. A run gives the same pass or fail result with harvesting on or off.
- **AC-18**: The workspace engine sends snapshots as a `harvest` list (`{url, elements}`, at most `harvest_max_pages` items) on both `completed` webhook payloads, the passed one and the failed one. The Python runner writes its snapshots to `harvest.json` in the run's artifact directory; `run_execution_and_stream` and the batch route in `backend/main.py` read that file for each run (each queue item has its own run directory) after the result is saved. Both paths call `ExecutionService.apply_object_repository_harvest(project_id, test_case_id, execution_id, snapshots)`, the only place that ingests them. Snapshots of failed runs are ingested too. A missing file or a malformed snapshot is skipped with a warning and never fails the webhook or the run.
- **AC-19**: A harvested page is ingested with the AC-1 and AC-2 rules (page matching or creation, fingerprints, naming, locator ordering, tester edited elements untouched, soft deleted fingerprints and page patterns not recreated, a `missing` element seen again set to `healthy`), except that a harvest never sets an element to `missing` and never changes `last_scanned_at`. Applying the same snapshot again changes nothing; this follows from fingerprint matching, so it needs no execution ID.
- **AC-20**: Nothing a run typed reaches the repository. The extractor never reads the value of an input, select, or textarea. The runner replaces every occurrence of the `TARGET_USERNAME` and `TARGET_PASSWORD` values in collected strings with `[redacted]`. The backend redacts again before ingest: the project's vault credentials and every value in the test case's `test_data` and every `test_data_sets` row. Redaction covers the label, placeholder, every attribute, and every locator value; it is case insensitive and ignores values shorter than 3 characters. A locator whose value was redacted is dropped (an element left with no locator is dropped). Attribute and locator values that look like tokens (32 or more characters of only hex, or only base64 characters) are dropped too. Snapshot URLs keep the path only.
- **AC-21**: Reconnaissance (scan and generation) and both runners use one extractor source and one strategy to match rule, so the same element seen by a scan and by a run gets the same match counts and the same fingerprint (computed after redaction on both sides) and ends up as one element.

## Decision

**Chosen option**: Option 2: Named keys in scripts, locators injected by the backend at read and dispatch time.

Reconnaissance and testers maintain a three table repository (pages, elements, locators). The generator writes `orx(page, 'page.element')` calls. The backend inserts a fixed helper and the current locators for the referenced keys whenever a script is read or sent to run, and turns run markers into element and locator health.

**Update (2026-11-03)**: test runs on both runners also harvest elements with the same extractor reconnaissance uses, and the backend ingests them with the recon merge rules minus marking missing. Chosen over letting reconnaissance log in with vault credentials (runner up), because runs already log in and follow the real user flow, so no new login logic and no form submissions are added to inspection.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

**Data model sketch** (PostgreSQL tables, mirrored as nested lists in the JSON store under each project):

`object_pages`
| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| project_id | UUID | FK `projects.id`, on delete cascade, indexed |
| name | string(200) | required, snake_case (`^[a-z][a-z0-9_]*$`), unique per project among non deleted |
| url_pattern | string(500) | required, path glob, unique per project among non deleted |
| last_scanned_at | datetime tz | nullable |
| created_at, updated_at | datetime tz | required |
| is_deleted, deleted_at, deleted_by | soft delete | `deleted_by` FK `users.id`, on delete set null |

`object_elements`
| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| page_id | UUID | FK `object_pages.id`, on delete cascade, indexed |
| name | string(100) | required, snake_case, unique per page among non deleted |
| element_type | string(20) | `button`, `link`, `input`, `select`, `checkbox`, `radio`, `textarea`, `nav` |
| description | text | nullable |
| fingerprint | string(64) | nullable, unique per page when not null (including soft deleted, so deleted elements block recreation) |
| health | string(20) | `healthy`, `needs_review`, `broken`, `missing`; default `healthy` |
| manually_edited | bool | default false |
| last_used_at | datetime tz | nullable |
| last_execution_id | UUID | nullable, plain reference, no FK |
| last_locator_index | int | nullable, 0 to 4 |
| failure_count | int | default 0 |
| consecutive_failed_runs | int | default 0; reset by any run with a win or by a tester save |
| applied_execution_ids | JSON | list of the last 20 execution IDs applied, newest last; default `[]` |
| created_at, updated_at | datetime tz | required |
| is_deleted, deleted_at, deleted_by | soft delete | as above |

`object_locators`
| Field | Type | Rules |
|---|---|---|
| id | UUID | PK |
| element_id | UUID | FK `object_elements.id`, on delete cascade, indexed |
| position | int | 0 is the main locator, max 4, unique `(element_id, position)` |
| strategy | string(20) | `role`, `label`, `placeholder`, `text`, `test_id`, `css`, `xpath` |
| value | text | required, not blank |
| options | JSON | default `{}`; `role` allows `name`, `exact`; `label` and `text` allow `exact`; others must be `{}` |
| success_count, failure_count | int | default 0 |
| last_succeeded_at | datetime tz | nullable |
| created_at, updated_at | datetime tz | required |

Relationships: project 1:N pages, page 1:N elements, element 1:N locators (1 to 5). The name uniqueness rules are partial unique indexes `WHERE is_deleted = false` in PostgreSQL, and service checks in the JSON store. The element key used in scripts is `<page.name>.<element.name>`.

**State transitions** (`object_elements.health`):
- any → `healthy`: a tester saves the element, a run where every marker for it won at index 0, or reconnaissance sees a `missing` element again
- any → `needs_review`: a run where a backup won, or the first run in a row where every locator failed; recon also creates an element as `needs_review` when every locator it built matches more than one element
- any → `broken`: the second run in a row where every locator failed
- recon owned, any → `missing`: reconnaissance scans the page and doesn't see the element (elements with `manually_edited = true` never go `missing`)

**Page matching** (decided here): a pattern is a path glob matched against the URL path only (no host, no query string, trailing slash ignored, case sensitive). `*` matches one path segment (`[^/]+`) and `**` matches any number of segments. When several patterns match, the one with the most literal characters wins. Ties go to the longer pattern, then the oldest page.

**Naming and fingerprint** (decided here):
- **Element name**: snake_case slug of the first non empty value among label or accessible name, placeholder, `name`, `id`, `data-testid`. Otherwise `<element_type>_<n>`. Cut to 100 characters, with `_2`, `_3` added on a clash.
- **Page pattern and name** (when recon creates a page): the pattern is the path with numeric and UUID segments replaced by `*` (`/booking/12345/details` → `/booking/*/details`); an empty path becomes `/`. The name is the slug of the literal segments (`booking_details`, `/` → `home`), with the same clash rule. If the pattern equals a soft deleted page's pattern, recon skips that page.
- **Fingerprint**: SHA-256 hex of `role|type|normalized accessible name|first stable attribute`, where the stable attribute is `data-testid`, else `id`, else `name`. When the accessible name and every stable attribute are all empty, the fingerprint is left empty: the element is still saved but is never matched on a later scan.
- **Name clash with a soft deleted element**: the partial unique index allows the name to be reused, and the fingerprint rule decides recreation (a soft deleted fingerprint is never recreated, while a different fingerprint is created under the free name).
- **Label changes**: when a recon owned element's label changes, its fingerprint changes, so recon creates a new element and the old one goes `missing`. Scripts keep the old key until a tester renames or merges. This is accepted for this version.

**Locators reconnaissance builds** (in this order, skipping empty or duplicate entries, at most 5):
1. `test_id`
2. `role` with `name`
3. `label` (form fields)
4. `placeholder`
5. `css` from `id` or `name`
6. the existing reconnaissance selector (as `css`, or `xpath` when it is one)

Recon measures `match_count` for each locator it builds (a `count()` per built locator on the live page). A locator matching more than one element never goes in position 0 while one matching exactly one exists. If every built locator matches more than one, the order above is kept and the element is created as `needs_review`. Locators that match nothing are dropped.

**Injected block** (plain JS, because the execution engine strips imports and runs the script through `new Function`; it must not contain `Function(`, `eval(`, or `require(`):
```js
// <object-repository> generated, do not edit
const __OBJREPO = { "login_page.login_button": [{ "s": "role", "v": "button", "o": { "name": "Log in" } }, { "s": "css", "v": "#login" }] };
const __OBJREPO_TIMEOUT = 3000;
async function orx(page, key) {
  const log = (m) => (typeof addLog === 'function' ? addLog(m) : console.log(m));
  const chain = __OBJREPO[key];
  if (!chain) { log('[OBJREPO] ' + JSON.stringify({ key, index: -1, tried: 0, ok: false })); throw new Error('Object repository element ' + key + ' is not in the repository'); }
  for (let i = 0; i < chain.length; i++) {
    const { s, v, o } = chain[i];
    const loc = (s === 'role' ? page.getByRole(v, o) : s === 'label' ? page.getByLabel(v, o)
      : s === 'placeholder' ? page.getByPlaceholder(v) : s === 'text' ? page.getByText(v, o)
      : s === 'test_id' ? page.getByTestId(v) : s === 'xpath' ? page.locator('xpath=' + v) : page.locator(v)).first();
    try { await loc.waitFor({ state: 'visible', timeout: __OBJREPO_TIMEOUT }); log('[OBJREPO] ' + JSON.stringify({ key, index: i, tried: i + 1, ok: true })); return loc; } catch (e) {}
  }
  log('[OBJREPO] ' + JSON.stringify({ key, index: chain.length, tried: chain.length, ok: false }));
  throw new Error('Object repository element ' + key + ' was not found by any locator');
}
// </object-repository>
```
Generated scripts use it like this: `const btn = await orx(page, 'login_page.login_button'); await btn.click();`. The Selenium version is a Python `orx(driver, key)` that returns a `WebElement` and logs no markers. Its strategies translate as follows:
- `css` and `xpath`: used directly
- `test_id`: `[data-testid="v"]`
- `placeholder`: `[placeholder="v"]`
- `text`: `//*[normalize-space()="v"]`
- `label`: `//label[normalize-space()="v"]/following::*[self::input or self::select or self::textarea][1]`
- `role` with `name`: `//*[(@role="v" or self::T) and (normalize-space()="name" or @aria-label="name")]`, where T is the native tag for the role (`button` → `button`, `link` → `a`, `textbox` → `input`, `combobox` → `select`, `checkbox` and `radio` → `input`); roles without a native tag use `@role` only

The Python block uses `# <object-repository>` and `# </object-repository>` markers. The block is inserted after the import lines. Locator values are serialized with `json.dumps` and every `(` is escaped as `(`. `strip_block` removes everything from the first start marker to the last end marker (a missing end marker removes from the start marker to the end of the helper definition, and duplicated blocks are removed together), so a hand edited or doubled block is cleaned and rendering again is idempotent (the same result every time).

**Harvest during execution** (decided here, update 2026-11-03):
- **Shared extractor**: one plain JS file, `backend/playwrightt/lib/object-harvest.js`, written as a single function expression `(options) => { ... }` with no `import`, `export`, or globals, so all three consumers can use its text as is. Run in the page, it lists visible buttons, links, form fields, and navigation links (main frame only, at most 300 elements) and returns, per element, `element_type`, `role`, accessible name (`label`), `placeholder`, and `attributes` (`data-testid`, `id`, `name`, `tag`, `type`, each cut to 100 characters) plus the candidate locators in the AC-1 order, each with its `match_count`. It never reads `.value` of an input, select, or textarea. Match counts are computed inside the page in the same call, with one strategy to match rule table in the file that mirrors how `orx` resolves each strategy (role and accessible name, label, placeholder, text, `data-testid`, CSS, XPath). One in page call per snapshot avoids a Playwright round trip per locator, which the Python runner's `slowMo: 1000` would turn into one second per count. Consumers: reconnaissance reads the file from disk and runs it with `page.evaluate`, replacing its `_object_element` code and its per locator `_count_object_locator` counting for objects; the workspace engine loads it as text (`fs.readFileSync`, the workspace has `allowJs`) and evaluates it; `PlaywrightRunner.run` reads it and embeds it in the generated `test.spec.ts` as a string literal. The result shape is the recon `_objects` item, so `ingest_recon` takes it unchanged.
- **When**: a listener on main frame navigations (`framenavigated` filtered to `page.mainFrame()`, which also fires on client side route changes) schedules a snapshot 1000 ms after the last navigation. When the timer fires, the runner checks the normalized path against its harvested set; if the path is new and the set is below `harvest_max_pages`, it adds the path and runs the snapshot. A snapshot whose URL path changed between start and end is discarded and its path removed from the set. Each snapshot has a `harvest_page_budget_ms` deadline; at test end the runner waits for a pending snapshot up to that deadline, then drops it. The runner's set is only an optimization: the backend dedupes by path (first snapshot wins) and applies the cap per execution, so worker restarts after a failed test can't break the rule.
- **Workspace engine**: the run's page is shared across executions (`ExecutionEngine.sharedPage`), so the listener is attached at the start of each execution and removed with `page.off` in a `finally` on both the success and the error path. The set and the collected snapshots live on the execution in `executionQueue`; both `completed` payloads carry `harvest`. The harvest settings are fetched once per run from `GET /api/v1/object-repository/harvest-settings` with a 2 second timeout; any failure means off.
- **Python runner**: the injected wrapper attaches the listener in its existing `beforeEach` (each test gets a new page) and clears the timer in an `afterEach` that runs before the runner's `storageState` `afterEach`. The set is kept at module level per worker process. Snapshots are appended to `harvest.json` in the run directory (one JSON array, rewritten after each snapshot), not printed to stdout, so large snapshots can't be truncated or mixed into the log stream. After the run, `run_execution_and_stream` reads `ARTIFACTS_ROOT/<test_case_id>/harvest.json`, and the batch route reads each queue item's own run directory, so attribution never depends on which item is "running".
- **Redaction**: in the runner, every collected string (label, placeholder, attributes, locator values) has the `TARGET_USERNAME` and `TARGET_PASSWORD` environment values (when 3 characters or longer) replaced with `[redacted]`, case insensitive. In the backend, `apply_object_repository_harvest` does the same with the project's vault credentials and every value in the test case's `test_data` and all `test_data_sets` rows, then drops locators containing `[redacted]`, drops attribute and locator values that look like tokens (32 or more characters of only hex, or only base64 characters), and drops elements left with no locator. Reconnaissance applies the same token rule, so scan and run fingerprints are computed from the same cleaned values.
- **Ingest**: `apply_object_repository_harvest(project_id, test_case_id, execution_id, snapshots)` validates and redacts, keeps the first snapshot per normalized path up to `harvest_max_pages`, and calls `ingest_recon(project_id, url, elements, mark_missing=False)` per page. With `mark_missing=False` the unseen element pass and the `last_scanned_at` write are skipped; everything else is the same code path as a scan. Idempotence comes from fingerprint matching, so `execution_id` is used only for logging.
- **No audit entry and no new table**: a harvest is a system write like recon during generation. It logs one info line per execution with the project, whether the project is a safe test environment, and the summed counts.

**API surface** (all under `/api/v1/projects/{project_id}/object-repository`, bearer auth, permission module `ObjectRepository`):
| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/pages` | GET | `limit` (default 50, max 200), `offset` | pages, each with `counts: {healthy, needs_review, broken, missing}`, plus `total` | view | 404 project |
| `/pages` | POST | `name` (req), `url_pattern` (req) | page | create | 409 duplicate, 422 invalid |
| `/pages/{page_id}` | PATCH | `name`, `url_pattern` (opt) | page, `scripts_rewritten` | edit | 404, 409, 422 |
| `/pages/{page_id}` | DELETE | none | `elements_deleted`, `referencing_test_cases` | delete | 404 |
| `/pages/{page_id}/elements` | GET | `health` (opt), `limit` (default 100, max 500), `offset` | elements with ordered locators, `total` | view | 404 |
| `/pages/{page_id}/elements` | POST | `name` (req), `element_type` (req), `locators` (req, 1 to 5), `description` (opt) | element | create | 409 duplicate, 422 invalid |
| `/elements/{element_id}` | PATCH | `name`, `element_type`, `description`, `locators` (full ordered list) (all opt) | element, `scripts_rewritten` | edit | 404, 409, 422 |
| `/elements/{element_id}` | DELETE | none | `referencing_test_cases` | delete | 404 |
| `/scan` | POST | none | 202, `scan_id` | create | 409 not a safe test environment or scan running |
| `/scan/{scan_id}` | GET | none | `status`, `pages_scanned`, `added`, `updated`, `marked_missing`, `error` | view | 404 unknown, expired, or belongs to another project |
| `/api/v1/object-repository/harvest-settings` (not under a project) | GET | none | `enabled`, `max_pages`, `budget_ms` | none (global settings, no secrets; called by the execution engine like its credentials fetch) | none |
| `/api/v1/projects/{project_id}/executions/webhook` (existing) | POST | `completed` payloads gain optional `harvest` (list of `{url, elements}`) | unchanged | unchanged | unchanged |

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| Recon ingest | page path | `urlparse(evidence.pages[].url).path` from `inspect_project` |
| Recon ingest | element role, label or name, placeholder, match count | existing reconnaissance element and field output |
| Recon ingest | `data-testid`, `id`, `name`, tag, `type` attributes | new `attributes` dict (values cut to 100 characters) that reconnaissance reads with `get_attribute` and `tagName` on each element and field; it goes to `ingest_recon` only and is removed from the evidence the prompt receives |
| Recon ingest | navigation links | new extraction of `a[href]` inside `nav` or `[role=navigation]` (max 50 per page), saved as `element_type = nav` and not also as `link` |
| Recon ingest | match count per built locator | `count()` of each built locator on the live page during reconnaissance |
| Scan | counts | `ingest_recon` returns `{added, updated, marked_missing}` per page, summed by the job |
| Recon ingest | element name, page name, fingerprint, locator order | derived by the naming, fingerprint, and locator rules above |
| Recon ingest | `element_type` | reconnaissance `role`/`type`: `button` → button, `link` → link, `textbox`/`text`/`email`/`password`/`search`/`number` → input, `combobox`/select → select, `checkbox`, `radio`, tag `textarea` → textarea, navigation extraction → nav |
| Scan | URLs to visit | project `target_url`, plus each page `url_pattern` without wildcards joined to the target origin, plus `_derive_urls_from_test_cases` over the project's test cases |
| Scan | target credentials | `VaultService`, as reconnaissance uses today |
| Scan | job status and counts | in memory scan registry entry (kept for `scan_status_ttl_seconds`) |
| Manual edit | `manually_edited`, `health`, `failure_count` | set by the service on every API create or update |
| Manual edit | audit user | `get_current_user` |
| Rename | `scripts_rewritten` | count of the project's test cases whose `playwright_script` contained `'old.key'` or `"old.key"` |
| Delete | `referencing_test_cases` | count of the project's test cases whose `playwright_script` contains the key |
| Prompt | URLs the test visits | `_derive_urls_from_test_cases(project.target_url, [test_case])` plus the `target_url` path |
| Prompt | element list and order | repository query, filtered and sorted per AC-6 |
| Prompt | cap | `object_repository.max_prompt_elements` in `default_config.yaml` |
| Render | locators per key | repository lookup of keys found by regex `orx\(\s*\w+\s*,\s*['"]([a-z0-9_]+\.[a-z0-9_]+)['"]` |
| Render | locator timeout | `object_repository.locator_timeout_ms` |
| Run feedback (Python path) | marker lines | `[OBJREPO]` lines collected from `on_log_callback` in `run_execution_and_stream`, passed to `finalize_execution` |
| Run feedback (workspace path) | marker lines | new `logs` field on the engine's `completed` webhook payloads, holding only `[OBJREPO]` lines from `artifacts.logs`, capped at 2000 |
| Run feedback | outcome per element | worst marker for that key in the execution (`broken` > `needs_review` > `healthy`) |
| Run feedback | locators to count as failed | marker `index` and `tried` |
| Render | project | the test case's scenario → requirement → project, never a query parameter |
| Run feedback | `execution_id` | webhook `payload.execution_id` or the `execution_id` argument of `run_execution_and_stream` |
| Harvest | elements, attributes, candidate locators | the shared extractor `backend/playwrightt/lib/object-harvest.js`, run on the live page of the run |
| Harvest | match count per candidate | the shared extractor's in page strategy to match table, in the same `page.evaluate` call as the collection |
| Harvest | page URL | the main frame URL when the snapshot is taken, path only |
| Harvest | which pages, how many | runner: the execution's harvested path set; backend (authoritative): first snapshot per normalized path, capped by `object_repository.harvest_max_pages` |
| Harvest | page path normalization | the page matching rule: path only, trailing slash removed, case kept, hash fragment ignored |
| Harvest (Python path) | on or off, page cap, deadline | `object_repository.harvest_enabled`, `harvest_max_pages`, `harvest_page_budget_ms`, which `PlaywrightRunner.run` reads from settings and passes to the subprocess as `OBJREPO_HARVEST` (`1` or `0`), `OBJREPO_HARVEST_MAX_PAGES`, `OBJREPO_HARVEST_BUDGET_MS` |
| Harvest (workspace path) | on or off, page cap, deadline | the same three settings, fetched by the engine at run start from `GET /api/v1/object-repository/harvest-settings` (next to its existing credentials fetch); a failed fetch means harvesting is off for that run |
| Harvest (workspace path) | snapshots | `harvest` field of the `completed` webhook payload |
| Harvest (Python path) | snapshots | `harvest.json` in the run directory: `ARTIFACTS_ROOT/<test_case_id>/` for a single run, the queue item's run directory in a batch |
| Harvest | values to redact in the runner | `TARGET_USERNAME`, `TARGET_PASSWORD` environment values the runner already receives from `VaultService` |
| Harvest | values to redact in the backend | `VaultService().get_credentials(project_id)` plus the executed test case's `test_data` and `test_data_sets` values |
| Harvest | test case for redaction | webhook `payload.test_case_id`, the `test_case_id` argument of `run_execution_and_stream`, or the batch queue item's `testcase_id` |
| Harvest | settings fetch timeout (workspace) | fixed at 2000 ms in the engine |
| Harvest | element cap per snapshot | fixed at 300 in the shared extractor |

**Key invariants**:
- Each element has 1 to 5 locators with contiguous positions starting at 0.
- Element keys are unique within a project among non deleted rows.
- Reconnaissance never writes to an element where `manually_edited = true`.
- Stored `playwright_script` never contains the `<object-repository>` block.
- One execution updates each element at most once (skip when its ID is in `applied_execution_ids`).
- In the JSON store, every multi entity write (rename rewrite, recon ingest of a page, locator replacement) is one `_read_raw` → change → `_write_raw` cycle under the existing file lock, so nothing interleaves.
- The scan registry is per process. Only one scan per project can be `queued` or `running`. A scan still running after 10 minutes is marked `failed`.
- No credentials, cookies, or entered values are stored in the repository. Locator values come from page attributes and visible labels only, run through the existing `_sanitize_text`.
- A harvest never sets `missing` and never writes `last_scanned_at`; only a scan or generation time reconnaissance does.
- Reconnaissance and both runners share one extractor source; there is no second copy of the element rules.
- Harvesting is best effort: no code path lets a harvest error, timeout, or ingest failure change a run's result or fail a webhook.

**Security model**:
- **Permission module `ObjectRepository`** (seeded): Admin gets all actions (automatically, through the existing module loop). Project Manager gets `view`, `create`, `edit`, `delete`. Tester gets `view`, `create`, `edit`, matching how Tester is set up for other modules (no delete). Stakeholder gets `view`. Project scoped role mappings apply through the existing `has_permission`.
- All reads and writes are scoped by `project_id` from the path. Element and page IDs from another project return 404.
- A scan only runs for a project with `is_safe_test_environment = true`, reusing reconnaissance's existing network restrictions. No compliance scope applies.

**Configuration required**: no new environment variables. Add to `backend/config/default_config.yaml`:
- `object_repository.max_prompt_elements: 150`
- `object_repository.locator_timeout_ms: 3000`
- `object_repository.scan_status_ttl_seconds: 3600`
- `object_repository.harvest_enabled: true` (global kill switch; harvesting runs on every project)
- `object_repository.harvest_max_pages: 20`
- `object_repository.harvest_page_budget_ms: 5000`

**Critical test scenarios**:
- Happy path: a tester creates `login_page.login_button`, a script is generated that uses `orx`, the run uses the main locator, and the element stays `healthy` with `last_execution_id` set. Verifies **AC-4**, **AC-6**, **AC-8**, **AC-9**, **AC-10**.
- Locator fix: the main locator is changed after generation, and the next run's dispatched script contains the new locator without regeneration. Verifies **AC-8**.
- Fallback: the main locator is missing on the page, index 1 wins, the element becomes `needs_review`, and the counters move. Verifies **AC-9**, **AC-10**.
- Repeated key: a data driven run uses one key 20 times with one total failure, so the element gets one update with the worst outcome and counters move by 1. Two such runs in a row make it `broken`. Verifies **AC-10**.
- Hostile text: a label containing `eval(` and quotes renders a block that passes `validateScript` and still finds the element. Verifies **AC-9**.
- Strip: saving a script with a hand edited or doubled block stores keys only, and render then strip returns the original. Verifies **AC-8**.
- Recon merge: a second scan changes a recon owned element's locators, leaves a manually edited element unchanged, marks an unseen element `missing`, and doesn't recreate a deleted fingerprint. Verifies **AC-1**, **AC-2**.
- Concurrency: a tester PATCH and a recon write on the same element finish with the tester's locators and `manually_edited = true`. Verifies **AC-15**.
- Unknown key: the model invents `login_page.ghost`, one repair runs, and the test case ends unverified with the key named. Verifies **AC-7**.
- Duplicate webhook: the same `completed` payload processed twice changes the counters once. Verifies **AC-10**.
- Both stores: the repository tests run against the JSON store and against PostgreSQL, including rollback after an injected `SQLAlchemyError`. Verifies **AC-14**.
- Auth/permission: a Stakeholder calling PATCH gets 403 and a Tester calling DELETE gets 403. Verifies **AC-11**.
- Harvest behind login: a run that logs in and opens `/inventory.html` and `/cart.html` adds both pages and their elements; an element not seen on this run keeps its health. Verifies **AC-16**, **AC-19**.
- Once per page: a data driven run of 20 datasets over the same three pages produces exactly three snapshots. Verifies **AC-16**.
- Harvest can't break a run: an extractor that throws, and one that never returns, leave the run's pass or fail result and duration (within the budget) unchanged. Verifies **AC-17**.
- Both transports: a workspace `completed` payload with `harvest` (passed and failed runs), and a Python runner `harvest.json`, ingest the same elements; a batch of two test cases attributes each file to its own test case. Verifies **AC-18**.
- Shared page: two workspace executions in a row on the shared page each harvest only their own pages; the first run's listener is gone. Verifies **AC-16**, **AC-18**.
- Worker restart: a data driven Python run whose failed test restarts the worker still ingests each path once and respects the cap. Verifies **AC-16**.
- Speed under slowMo: a Python run (with `slowMo: 1000`) finishes each snapshot within the budget. Verifies **AC-17**.
- Redaction: a page showing "Welcome standard_user" and a test data value in a heading stores neither, from either runner; a locator built from a redacted label is dropped; a 1 character test data value redacts nothing; an `id` that is a 40 character hex token is dropped. Verifies **AC-20**.
- Parity: a scan and a run over the same page produce one element per control, not two. Verifies **AC-21**.
- Kill switch: with `harvest_enabled: false` no snapshot is taken on either runner. Verifies **AC-16**.

## Build plan

Tracer Bullet: the first slice carries one manually created element through the UI, API, validation, persistence, generation, and a real run. Later slices thicken it.

**Slice 1: one element, end to end**
1. Add the domain models, the Alembic migration for all three tables (with partial unique indexes), the JSON store mirroring, and repository methods for pages, elements, and locators in both stores, with rollback and session clearing on SQLAlchemy errors. Satisfies **AC-14**.
2. Add `ObjectRepositoryService` with page and element create, read, and list (validation, snake_case, glob, locator rules, `manually_edited` handling), the routes for `GET/POST /pages` and `GET/POST /pages/{id}/elements` in `backend/main.py` (kept thin), the seeded `ObjectRepository` permission module, and audit entries on create. Satisfies **AC-4**, **AC-11**, **AC-12**.
3. Add `render_script` and `strip_block` for Playwright (JS) and Selenium (Python), with escaped JSON values. Wire them in: strip in `project_service`'s test case script update, render in one `TestCase` response mapper used by every script response in `backend/main.py`, in the `/api/v1/playwright/workspace` responses (project resolved from the test case), and in `ExecutionAgent` when building each `QueueItem`. Satisfies **AC-8**, **AC-9**.
4. Feed the matched page elements into `PlaywrightAgent` generation (the `orx` instructions in `functional_script_generator/roles_and_responsibilities.md`, the cap, the ordering) and add unknown key checks to script validation with one repair. Satisfies **AC-6**, **AC-7**.
5. Add a minimal Object Repository sidebar view: a page list, an element table, and a create element form, behind `view` and `create`. Satisfies **AC-13** (partial), **AC-11**.

**Slice 2: run feedback**
6. Add `logs` (only `[OBJREPO]` lines, capped at 2000) to both `completed` webhook payloads in `execution-engine.ts`. Have `run_execution_and_stream` pass its collected marker lines to `finalize_execution`. In `ExecutionService.finalize_execution` only, group markers per key, then apply the worst outcome, the two run rule for `broken`, the counters, and `applied_execution_ids` deduping. Satisfies **AC-10**.

**Slice 3: reconnaissance fills the repository**
7. Add the `attributes` dict, a match count per built locator, and navigation link extraction to reconnaissance, keeping the prompt evidence unchanged. Add `ObjectRepositoryService.ingest_recon` with page matching or creation (numeric and UUID segments become `*`), naming, fingerprint (empty when nothing stable), locator building, merge rules, row locking, and returned counts. Call it after `inspect_project` in `workflow_service` and `grounded_playwright_service`. Satisfies **AC-1**, **AC-2**, **AC-15**.
8. Add the scan job: a per process in memory registry, a background task, the `POST /scan` and `GET /scan/{id}` routes (scan ID checked against the project), the safe environment and single scan guards, the 10 minute stale timeout, and a summary audit entry. Satisfies **AC-3**, **AC-12**.

**Slice 4: editing and the full UI**
9. Add `PATCH` and `DELETE` for pages and elements: full locator list replacement that keeps counters, a rename that rewrites references in scripts in the same transaction, delete reference counts, and audit with before and after. Satisfies **AC-4**, **AC-5**, **AC-12**.
10. Complete the UI: health counts and filters, the locator edit drawer (add, remove, reorder), Scan now with progress polling, empty, loading, and error states, and keyboard and text badges. Satisfies **AC-13**.
11. Update `docs/api.md`, `docs/workflow.md`, and add `docs/features/object-repository.md`. Add focused pytest coverage for every critical test scenario and a Playwright browser test for the view. Satisfies **AC-1** through **AC-15**.

**Slice 5: harvest during execution** (update 2026-11-03, Tracer Bullet: carry one harvested page through the Python runner to the repository first, then add the workspace path)
12. Write `backend/playwrightt/lib/object-harvest.js` (one function expression: collector, in page strategy to match table, 300 element cap, token rule) and switch reconnaissance's `_extract_object_elements` to run it through `page.evaluate`. Add a parity test on a local page comparing its counts with Playwright locator counts for each strategy. Satisfies **AC-21**.
13. Add `mark_missing` to `ingest_recon`, add `ExecutionService.apply_object_repository_harvest` (parse, cap, backend redaction, ingest, never raise), the three settings, and `GET /api/v1/object-repository/harvest-settings`. Satisfies **AC-18**, **AC-19**, **AC-20**.
14. Python runner: embed the extractor, the `beforeEach` listener and `afterEach` cleanup, the once per path set, the deadline, runner redaction, and the `harvest.json` writer into the generated spec behind `OBJREPO_HARVEST`; read the file in `run_execution_and_stream` and per queue item in the batch route, and hand it to the service after the result is saved. Satisfies **AC-16**, **AC-17**, **AC-18**, **AC-20**.
15. Workspace engine: fetch the harvest settings (2 second timeout), attach the listener per execution and remove it in `finally`, keep the set and snapshots on the execution, and add `harvest` to both `completed` payloads. Satisfies **AC-16**, **AC-17**, **AC-18**.
16. Update `docs/features/object-repository.md`, `docs/api.md`, and `docs/workflow.md`; add pytest coverage for ingest without missing, redaction, transport parsing, and the kill switch, plus a runner level test with a local page (both runners) for once per page, the deadline, and parity. Satisfies **AC-16** through **AC-21**.

## Consequences

**Positive**:
- One locator fix reaches every script on its next run, because locators are injected at dispatch, not baked in at generation.
- Scripts get shorter and the model stops retyping selectors, which removes a whole class of made up locators.
- Element health makes locator decay visible before it breaks a release run.

**Negative / tradeoffs**:
- Stored scripts can't run on their own outside this system (they need the rendered block). Anyone copying a script must take it from a rendered response.
- Renaming rewrites stored scripts in bulk. A wrong rename touches many test cases at once (it's audited, but there is no undo).
- The runner changes in one place: both `completed` payloads gain `logs`. Without that, workspace path markers would arrive as fire and forget webhooks after completion and get lost.
- Soft delete follows the codebase convention but needs partial unique indexes, and keeps deleted fingerprints around on purpose.
- Selenium scripts get stored locators but no health feedback, because Selenium can't run yet.
- The scan status lives in memory per process, so a restart loses an in flight scan's status (the data it saved stays). Running several uvicorn workers would split the registry, which is fine while the app runs as one process.
- An element whose locators all fail costs up to 15 seconds (5 locators × 3000 ms) before the step errors.
- Recon gets slower, because it now counts matches for every locator it builds.
- Unnamed elements with no stable attribute get no fingerprint, so a later scan adds them again as new elements instead of updating them.
- Harvesting runs on every project, as the engineer chose, not only safe test environments. It only reads pages, but page structure from targets nobody marked safe now lands in the repository. `harvest_enabled` is a global switch only; turning it off for one project needs a later per project setting.
- Runs get slower on their first visit to each page: up to `harvest_page_budget_ms` per new path (the snapshot runs beside the script, but its counts compete with it), plus at most one budget wait at test end.
- A run sees pages mid flow, so harvested elements can include controls that exist only in that state (an error banner's dismiss button, an open dialog). They are never marked missing by a run; only a scan cleans them up, and a scan can't reach pages behind login. Testers delete them by hand.
- Unnamed elements without a fingerprint are added again on every run that visits their page. This was a scan only cost before; with runs it repeats often. Teams with many such elements should watch the counts.
- Redaction is by exact value. A credential or test data value the page shows transformed (uppercased, partly masked) is not caught.
- Two transports to maintain (a payload field and a `harvest.json` file), and one more internal unauthenticated settings endpoint next to the engine's credentials fetch.
- Hash routed single page apps (`#/cart`, `#/checkout`) collapse into one path, so only their first settled view is harvested. Page matching ignores the hash too, so this is consistent, but such apps get little from harvesting.
- The in page match counts must stay in step with how `orx` resolves locators; the parity test is the guard, and any change to `orx` strategies needs the table updated.
- A dropped redacted locator can leave an element with fewer backups than a scan would build.

**Neutral**:
- `LocatorResolver` stays unused. The `orx` helper replaces its role at run time.
- The imported framework (`pom_index.json`) path keeps working and is independent of this feature.

## Follow-up

- [ ] Decide whether to delete `backend/services/locator_resolver.py` and `docs/features/locator-fallback.md`, or fold their strategy list into the reconnaissance locator builder.
- [ ] Consider a "Test locator" action (`POST .../elements/{id}/check`) that checks locators against the live page.
- [ ] Add Selenium health feedback once Selenium execution exists.
- [ ] Decide how the imported POM framework and the object repository should relate (for example, page classes that wrap stored elements).
- [ ] Consider a per project harvest switch if a team needs harvesting off for one target only.
- [ ] Consider a way to retire harvest only elements on pages behind login (for example, a "not seen in N runs" rule), since scans can't reach those pages to mark them missing.