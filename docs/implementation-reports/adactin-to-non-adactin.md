# Adactin → Non-Adactin (Target-Agnostic) Conversion

Date: 2026-10-11
Scope: backend + prompts + config + docs. **No frontend changes** (per instruction).

## Objective

Remove all hardcoded coupling to the Adactin Hotel demo application
(`https://adactinhotelapp.com/`, `ADACTINFORQA`, hotel booking page flow,
Adactin selectors) so the platform tests **any** project whose `target_url`
and credentials are configured per project. Adactin remains usable only as
one ordinary project among others — never as the assumed default.

## Design decisions

1. **No default target application.** Every `target_url` / `base_url` default
   that previously pointed at Adactin is now `None` / `""`. A project must
   supply its own `target_url` (Project Settings UI or
   `PUT /api/v1/projects/{id}`); credentials come from the vault.
2. **No hardcoded credentials.** The `ADACTINFORQA` / `hanish13` fallbacks are
   removed. When no credentials exist, agents log it and generated scripts use
   `TARGET_USERNAME` / `TARGET_PASSWORD` env placeholders.
3. **No hardcoded page flow.** `app_states.json` and both navigation planners
   (Python + TS mirror) now model a generic lifecycle
   (`LOGIN → HOME/DASHBOARD → LIST → DETAIL → FORM → CONFIRMATION → LOGOUT`)
   with `navigate_direct` fallback. Unknown apps resolve to `UNKNOWN` and
   navigate directly to the project's own `target_url`.
4. **No Adactin in LLM prompts.** The embedded Adactin reference guide is
   removed from all prompt templates and the Playwright system prompt; the LLM
   must derive locators/values only from the test-case inputs and `$base_url`.

## Changes (by file)

### Defaults — target URL no longer Adactin
| File | Change |
|---|---|
| `backend/models/project.py:21` | `Project.target_url` default `"https://adactinhotelapp.com/"` → `None` |
| `backend/schemas/project_dto.py:12,26` | `ProjectCreate.target_url` and `ProjectResponse.target_url` defaults → `None` |
| `backend/database/db_models.py:35` | `ProjectDB.target_url` column default → `None` (code-level only; no migration needed — existing rows keep their values, new rows get `NULL`) |
| `backend/repository/project_repository.py:29,317` | Both `create_project()` signatures (abstract + JSON impl) default `target_url` → `None` |
| `backend/repository/postgres_project_repository.py:121` | `create_project()` default `target_url` → `None` |
| `backend/services/project_service.py:28` | `create_project()` default `target_url` → `None` |
| `backend/config/settings.py:86` | `PlaywrightConfig.base_url` default → `""` |
| `backend/config/default_config.yaml:40-43` | `playwright.base_url` → `""` with comment pointing at per-project `target_url` |
| `backend/services/report_service.py:431` | Report `web_url` fallback chain ends in `""` instead of the Adactin URL |
| `backend/main.py:237` | `resolve_project_response` fallback `getattr(p, "target_url", …)` → `None` |
| `docs/api.md:55` | `ProjectCreate` example `target_url` → `"https://your-app.example.com/"` |

### Credentials — hardcoded Adactin login removed
| File | Change |
|---|---|
| `backend/agents/playwright_agent.py:206-213` | Removed vault-scan + `ADACTINFORQA`/`hanish13` hard fallback; logs a warning and leaves credentials `None` (scripts fall back to `default_username`/`default_password` placeholders + env vars) |
| `backend/main.py:1573-1581` | Removed `ADACTINFORQA`/`hanish13` hard fallback in the credentials endpoint; returns vault values or `None` |
| `backend/database/update_credentials.py` | Rewrote utility: previously overwrote **every** project with the Adactin URL/credentials; now **refuses to run** unless `TARGET_URL`, `TARGET_USERNAME`, `TARGET_PASSWORD` are set explicitly |
| `backend/services/jira_service.py:91-92` | Mock-mode JIRA issue changed from "Book a Hotel room in Adactin …" to a generic sample story |

### Page flow / state model — generic lifecycle
| File | Change |
|---|---|
| `backend/config/app_states.json` | Replaced 7 Adactin states (`adactinhotelapp.com`, `SearchHotel.aspx`, `SelectHotel.aspx`, `BookHotel.aspx`, `BookingConfirm.aspx`, …) with 8 generic states (`LOGIN`, `HOME`, `DASHBOARD`, `LIST`, `DETAIL`, `FORM`, `CONFIRMATION`, `LOGOUT`) matched by generic title/element hints, no vendor URL |
| `backend/services/navigation_planner.py` | Replaced Adactin transition graph (`SEARCH`/`RESULTS`/`BOOKING`, `fill_search_fields`, `select_hotel`, …) with generic transitions over the lifecycle states; constructor accepts an injectable per-project graph; BFS + `navigate_direct` fallback unchanged |
| `backend/playwrightt/lib/navigation-planner.ts` | Same replacement in the TS runner-side mirror |
| `backend/services/batch_queue_manager.py:218-229` | `_make_login_conditional` generalized: was keyed on `SearchHotel.php` + `#username`/`#login`; now detects a generic username→login-click sequence and checks for a generic login page/username field |
| `backend/services/app_state_resolver.py`, `backend/playwrightt/lib/app-state-resolver.ts` | No code change needed — both already read `app_states.json` generically and return `UNKNOWN` for unrecognized apps |

### LLM prompts — Adactin reference guide removed
| File | Change |
|---|---|
| `backend/prompts/scenario_prompt.txt` | Removed ~260-line embedded `<adactin_reference_guide>`; replaced with a target-agnostic application-context paragraph (no new `$vars`, so `ScenarioAgent`'s `title/description/count` substitution is unaffected) |
| `backend/prompts/test_case_prompt.txt` | Same removal/replacement (existing `title/description/scenarios/count` substitution unaffected) |
| `backend/prompts/playwright_prompt.txt` | Same removal; replacement references the project's own `$base_url` + vault credentials (existing substitution vars unchanged) |
| `backend/agents/playwright_agent.py:55-64` (docstring) | "Critical Rules for Adactin Hotel Application" → target-agnostic rules |
| `backend/agents/playwright_agent.py:75-112` (`_SYSTEM_PROMPT`) | Replaced 12 Adactin selector rules (`input#login`, `select#location`, `SearchHotel.php`, `BookingConfirm.php`, order-ID extraction, …) with 12 generic rules: inputs + `$base_url` are the only source of truth, accessible-locator priority, TODO comments instead of fabricated locators, generic post-login wait, no invented URLs/formats |
| `backend/agents/playwright_agent.py:225-231` | Removed `adactin_reference_guide.md` file loading |
| `backend/agents/playwright_agent.py:327-343` | Removed Adactin guide injection into the system prompt |
| `backend/agents/playwright_agent.py:292,372` | Auto-login prompt text referencing `SearchHotel.aspx`/`#location` → generic post-login landing wait |
| `backend/prompts/agents/functional_script_generator/roles_and_responsibilities.md` | Replaced "DOM Selector Guide (Adactin Hotel Application)" (5 pages of Adactin selectors) with a target-agnostic selector guide; removed `<input>`-vs-`<button>` Adactin rule and Adactin field-name examples |

### Tests
| File | Change |
|---|---|
| `backend/tests/test_batch_execution_core.py:108-125` | `test_navigation_planner` updated to the generic graph (`LOGIN→DASHBOARD`, `LIST→CONFIRMATION` via `FORM`, same-state, `UNKNOWN` fallback). Other tests in the file use injected configs/custom states and are unaffected |

## Verification performed

- `py_compile` passes on all 15 edited Python files.
- `backend/config/app_states.json` parses as JSON; `default_config.yaml` structure eyeball-checked (no `yaml` module available in this env).
- Template-substitution check (stdlib `string.Template`, exact variable sets each agent passes): `scenario_prompt.txt`, `test_case_prompt.txt`, `playwright_prompt.txt`, and `functional_script_generator/roles_and_responsibilities.md` all substitute cleanly with **zero** remaining `adactin` mentions.
- `pytest` could **not** be executed (not installed in this environment), so the suite was not run — see follow-ups.

## Intentionally untouched

- **Frontend** (`frontend/js/app.js:3880` fallback `|| 'https://adactinhotelapp.com/'`, `frontend/index.html:874` placeholder): left per the no-frontend-changes constraint — see follow-up #1.
- **Existing data** (`backend/database/project_store.json`, `local_fallback.db`): historical projects/requirements still reference Adactin. Code no longer assumes it; update each project's `target_url`/vault or use `reset_demo_data()` for old demo projects.
- **Historical artifacts** (`backend/playwright*/artifacts/*/test.spec.ts`, `run_discovery.json`): past execution outputs, not runtime behavior.
- **One-off scripts** (`backend/database/fix_test_case_script.py`, `backend/database/update_testcase_scripts.py`): legacy Adactin data-fix scripts; not imported at runtime.
- **`reset_demo_data` keyword list** (`project_repository.py:867`, `postgres_project_repository.py:1131`): keeps `"adactin"` as a *cleanup* keyword so old Adactin demo projects are still purged — desirable, not coupling.
- **`test_bug_condition_exploration.py:61`**: Adactin URL inside a simulated script string is incidental fixture data (sibling test uses `example.com`); assertions don't depend on it.
- **Docs history** (`docs/guides/adactin-reference.md`, `docs/guides/playwright-fix.md`, `docs/features/report-specification.md`, implementation reports): retained as historical reference/examples. `adactin-reference.md` is now a **legacy example**, not a source of truth — no prompt or agent loads it anymore.

## Follow-ups (not done, out of scope)

1. **Frontend default** (requires frontend change): replace the `|| 'https://adactinhotelapp.com/'` fallback in `frontend/js/app.js:3880` and the placeholder in `frontend/index.html:874` with empty/generic values; otherwise the Settings UI still suggests Adactin when `target_url` is unset.
2. **Run the suite**: `pytest backend/tests/` was unavailable here; run it (especially `test_batch_execution_core.py`, `test_playwright_agent.py`, `test_api.py`) in an env with dev dependencies.
3. **Per-project states (optional enhancement)**: `ApplicationStateResolver`/`NavigationPlanner` accept injected configs, but there is no API/UI yet to store a per-project `app_states` graph — worthwhile if projects need custom state tracking.
4. **Existing rows**: projects created before this change keep their stored Adactin `target_url` until edited; consider a data patch or `reset_demo_data()` if a clean slate is wanted.
