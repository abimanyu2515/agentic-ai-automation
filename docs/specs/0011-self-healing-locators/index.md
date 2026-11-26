# 0011. Heal broken object repository locators after a run, repair the script from the RCA, and rerun

**Date**: 2026-11-25 (amended 2026-11-26: RCA driven script repair, AC-18 to AC-21, two stage verify in AC-10; updated 2026-11-26 to record the built script heal store and the `/testcases` route path)
**Status**: In Progress

## Summary

When every stored locator for an element fails during a run, a new self healing agent looks for the same element in the page the run harvested (the elements the run collected from each page it visited). It scores the candidates with fixed rules and asks the LLM only when the answer is unclear. Even then, the LLM can only pick one of the candidates.

A confident match is applied to the object repository right away, and the test case is rerun once. A less certain match is saved as a suggestion a tester can accept or reject. If the rerun still fails, the heal is undone and turned into a suggestion. Scripts hold only element keys, so a heal fixes every script that uses the element, with no script regenerated.

**Amendment (2026-11-26): script repair from the RCA.** A locator heal can't fix a failure outside a locator, such as a changed step order, a stale assertion text, or a timing problem. Healing now runs in two stages, each followed by one rerun of the failed test case only:
1. **Stage 1, locator heal** (unchanged, AC-1 to AC-10): it runs when the run has `ok: false` markers.
2. **Stage 2, script repair** (AC-18 to AC-21): it runs when the test still fails, every locator resolves, and the log analyser's RCA says `Flaky Test / Automation Issue`. The script is regenerated through the existing `PlaywrightAgent` repair path, with the RCA as repair feedback and the healed object repository keys in the prompt. It is applied only when it passes the gates, and then rerun.

A script fix whose rerun fails is restored and kept as a suggestion. Any locator heal still waiting on that rerun becomes a suggestion too. A `Product Bug` or `Test Environment Issue` RCA never changes a script. When every result passed, nothing is processed. There are at most 2 reruns per graph run.

## Requirements

**User stories**:
- As a tester, I want a renamed button or a changed test id to be fixed automatically, so that one UI change doesn't fail every run until someone edits the repository.
- As a tester, I want unsure matches held as suggestions I can accept or reject, so that the repository never quietly binds to the wrong element.
- As a tester, I want the report to show what was healed and whether the rerun passed, so that I trust the result and can follow up.
- As a project manager, I want to turn self healing off for a project, so that teams that want full manual control keep it.
- As a tester, I want a script whose step or assertion broke for an automation reason (not a product bug) repaired from the failure analysis, so that one stale step doesn't fail every run.
- As a tester, I want a script fix that doesn't pass on rerun restored and held as a suggestion, so that a failed repair never stays in the test case.

**Scope note**: the graph EXECUTE path (`WorkflowService`, around `workflow_service.py:1092`) runs exactly one test case per invocation. Every rule below is written for that case. Where a rule mentions several test cases, it applies only to direct graph use (unit and graph tests). The batch and webhook paths don't heal in this slice.

**Acceptance criteria**:
- **AC-1 (trigger)**: `WorkflowService` sets the runtime field `state.healing_enabled` before it invokes the graph. It is true only when `object_repository.healing.enabled` is true and the project's `self_healing_enabled` is true.
  - After `execution_analysis_agent`, a heal target is an active element key with at least one `[OBJREPO]` marker `ok: false` (from any attempt) in the latest result of a test case whose status is `failed` or `error`. That result's `failure_category` must not be `Test Environment Issue`.
  - Markers in a passed result never produce targets.
  - A key that fails in several test cases is healed once.
  - When `healing_enabled` is false, or there are no targets, the graph goes straight to `bug_reporting_agent` and nothing is saved.
  - **No errors, no processing**: the graph also goes straight to `bug_reporting_agent`, saving nothing and starting no rerun, when either of these holds:
    - every latest result passed
    - there is no heal target and no script repair trigger (AC-18)
- **AC-2 (evidence)**: Candidates come only from the failed run's `harvest.json`, which is in `ARTIFACTS_ROOT / <test_case_id>` and holds the last attempt's snapshots.
  - They are read with `read_harvest_file` and taken from every snapshot whose path matches the element's page `url_pattern`, using the same glob match as page matching in `ingest_recon`.
  - Candidates are deduplicated by fingerprint, keeping the first one seen.
  - Every candidate passes through the same backend redaction `apply_object_repository_harvest` applies before it is scored or sent to the LLM.
  - No matching snapshot means no heal for that key, and the log line `no harvest evidence for <key>`. Aria snapshot and live inspect evidence are a follow-up.
- **AC-3 (evidence kept)**: In its heal phase, before routing to `execution_agent`, `self_healing_agent` copies these into `ARTIFACTS_ROOT / <test_case_id> / heal-<attempt>/`:
  - `report.json`, `harvest.json`, and `screenshots/` from the run directory
  - the files at `result.trace_path` and `result.video_path`
  - `error-context.md` from beside the trace

  A missing file is skipped. It then points the failed result's `screenshot_path`, `video_path`, and `trace_path` at the copies. Log analysis has already run by then, so it read the originals. The copies sit under the artifacts root, so `LogEvidenceCollector` path checks still accept them.
- **AC-4 (deterministic score)**: The expected signals are read from the element's stored locators, and the candidate signals from the candidate's locators and attributes.

  | Strategy | Signal |
  |---|---|
  | `test_id` | test id |
  | `role` | role, plus `options.name` as the name |
  | `label` | label |
  | `placeholder` | placeholder |
  | `text` | name |
  | `css` | id when the value is `#<id>`; name when it is `[name="<n>"]` |
  | `xpath` | none |

  Text is lowercased and its whitespace collapsed before comparing. Scores are computed as follows:
  - A candidate whose fingerprint equals the element's current fingerprint scores 1.0, because it is the same element with fresh locators.
  - Otherwise the score is the sum of these, clamped to 0 to 1:
    - test id equal: 0.35
    - role equal: 0.15
    - id or name equal: 0.15
    - up to 0.25 for the `difflib.SequenceMatcher` ratio of the name or label
    - placeholder equal: 0.10
    - minus 0.30 when `element_type` differs
  - A candidate whose fingerprint belongs to another active element on the same page is excluded.
  - On equal scores, the candidate with a test id or role match ranks first, then the first in harvest order.
- **AC-5 (decision)**: Let `top` be the best score and `margin` its lead over the second best (`margin = top` when there is one candidate). The rules are:
  - **Apply**: `top >= auto_apply_threshold` (0.85) and `margin >= min_margin` (0.15) produce `applied`, method `deterministic`.
  - **Unclear**: `top >= suggest_threshold` (0.5) that is not an apply goes to the LLM when `llm_enabled` is true. Otherwise it becomes `suggested`.
  - **No match**: `top < suggest_threshold` saves nothing.
  - **Edited by a tester**: an element with `manually_edited = true` never gets `applied`. Its best result becomes `suggested`.
- **AC-6 (LLM fallback)**: The LLM gets the element key, description, element type, old locators, and the top `max_candidates_for_llm` (5) candidates. Each candidate is passed as an index, its locators, its role, its label, and its score.
  - All page text (labels, names, descriptions) is wrapped in delimiters and marked as untrusted data. The prompt tells the model never to follow instructions found inside it.
  - It returns `HealSelectionResponse {candidate_index: int | null, confidence: float, reason: str}`. `confidence` is clamped to 0 to 1, and an index out of range counts as null.
  - Final confidence is `min(confidence, candidate_score + 0.2)`. The heal is `applied` (method `llm`) when final confidence `>= auto_apply_threshold`, `suggested` when it is `>= suggest_threshold`, and nothing otherwise.
  - When the LLM call fails, the top deterministic candidate becomes `suggested` if `top >= suggest_threshold`.
- **AC-7 (apply)**: The order is fixed:
  1. Apply the failed result's markers with `ExecutionService.apply_object_repository_markers(project_id, failed_result.id, lines)`, so failure counts land on the locators that actually failed.
  2. Load the repository and record each target's `locators_version`.
  3. Score and decide.
  4. Apply in one `ObjectRepositoryStore.mutate`.

  Inside that `mutate`:
  1. If `locators_version` differs from the heal's recorded value, the heal is saved as `suggested` instead.
  2. Otherwise the candidate's locators are ordered with `order_recon_locators` and placed first, the old locators follow as backups, and the list is capped at 5. Locators that drop off are the old ones with the highest `failure_count`.
  3. Unchanged locators keep their counters, through `_replace_locators`.
  4. The element's `fingerprint` becomes the candidate's fingerprint, `health` becomes `needs_review`, and `locators_version` goes up by 1.
  5. `manually_edited` is unchanged, a `LocatorHeal` row with status `applied` is written, and one audit entry records the old and new locators.

  `locators_version` changes only on tester create and update, heal apply, accept, and revert. Markers and harvest ingest never change it.
- **AC-8 (suggestion)**: A suggestion writes a `LocatorHeal` row with status `suggested`, the candidate's locators, and `candidate_fingerprint`, and changes no locator. An element has at most one `suggested` heal.
  - In the same `mutate`, an older suggestion is marked `rejected` with `reason = "replaced by a newer suggestion"`, `decided_by = null`, and `decided_at` set.
  - In PostgreSQL an `IntegrityError` on the partial unique index means another run won the race. The new suggestion is dropped with a warning, after a rollback and session clear.
  - While a suggestion is pending, `ingest_recon` skips any harvested element whose fingerprint equals a pending `candidate_fingerprint` on that page. It reads them from the unit it already loaded, so no duplicate element is created.
- **AC-9 (rerun)**: When at least one heal is `applied`:
  - `self_healing_agent` sets `heal_rerun_test_case_ids`, adds 1 to `heal_attempts`, and routes to `execution_agent`.
  - `execution_agent` runs only those test cases, with `RecoveryManager` retries off for this run and the same `state.execution_batch_id` as the first run (so the saved login session is reused). It then clears the list.
  - Each rerun result has `heal_rerun_of` set to the failed result's id, and `heal_ids` set to the heals it tests.
  - `execution_analysis_agent` analyses only results whose `failure_category` is still null.
  - The loop stops at `max_attempts_per_run` (default 1).
- **AC-10 (verify, two stages; amended 2026-11-26)**: After each rerun, `self_healing_agent` checks each applied heal against the latest result of every test case it was rerun for. "Revert a heal" below means:
  - In one `mutate`, the old locators and old fingerprint are put back and `locators_version` goes up by 1, with the AC-7 version check against the version the apply left. If a tester changed the element meanwhile, the locators are left alone and a warning is logged.
  - The heal becomes `suggested` and keeps its `candidate_fingerprint`, so the post run harvest ingest skips that candidate (AC-8). No second locator heal is tried.

  After the **stage 1 rerun** (locator heal):
  - **All passed**: the heal stays `applied`. Stage 2 does not run.
  - **The healed key failed again** (an `ok: false` marker for that key in the rerun): revert the heal. Stage 2 does not run.
  - **The healed key resolved** (only `ok: true` markers for it), the test case still failed, and AC-18 holds for the rerun result: the heal stays `applied` and its `heal_results` entry gets `verification: "pending_script_repair"`. The graph goes on to stage 2.
  - **Anything else** (a `Product Bug` or `Test Environment Issue` RCA, script repair off, or the case not eligible): revert the heal.

  After the **stage 2 rerun** (script repair, AC-21):
  - **Passed**: the script fix stays `applied`, and every `pending_script_repair` heal is kept (`verification: "passed"`).
  - **Failed or errored**: the script is restored (AC-21), and every `pending_script_repair` heal for that test case is reverted. Nothing stays applied unless the test passed with it.
- **AC-11 (results and report)**: Every reader uses the latest result for each test case (last appended): BugReportingAgent, ReportAgent, `WorkflowService` (in place of `execution_results[0]`), and `langsmith_tracer.py` (in place of `execution_results[0]`). So a test case that passed on rerun gets no bug report. `WorkflowService` then:
  - saves every result, giving the caller's `execution_id` to the latest one and relinking only that one's bug report
  - applies the rerun's markers under the latest result's id
  - ingests the harvest from the run directory, which now holds the rerun's harvest
  - runs knowledge indexing, the Jira finished sync, and report compiling for the latest result only

  `run_history` leaves out any result that another result's `heal_rerun_of` points at, so a healed attempt doesn't raise `flaky_score`. The report shows a latest result that passed with `heal_rerun_of` set as `Passed after heal`. This is a display label, not a stored status. A `Self healing` section lists each heal: element key, old main locator, new main locator, confidence, method, and the rerun outcome.
  - **Script repair addition**: the `Self healing` section also lists each script fix: test case, RCA category, root cause summary, rerun outcome, and a link to the old and new script diff. `Passed after heal` also covers a pass after a script fix. With two stages a test case can have up to 3 results. Every reader still takes the last one, and `run_history` leaves out every result that a `heal_rerun_of` points at (up to 2).
- **AC-12 (API)**: These routes exist:
  - `GET .../object-repository/heals?status=` lists the project's heals. Status is optional and defaults to all. Results are newest first, with no pagination (retention bounds them).
  - `POST .../heals/{heal_id}/accept` applies a `suggested` heal with the AC-7 apply steps (checked against the heal's recorded `locators_version`) and sets `accepted`, `decided_by`, and `decided_at`. It keeps `manually_edited` as it was and does not rerun anything.
  - `POST .../heals/{heal_id}/reject` sets `rejected`.
  - `POST .../heals/{heal_id}/revert` puts back `old_locators` and `old_fingerprint` for an `applied` or `accepted` heal, adds 1 to `locators_version`, and sets `reverted`.

  The errors are:
  - An unknown heal, one in another project, or one whose element is soft deleted: 404.
  - A heal in the wrong status: 409 `heal is <status>`.
  - A `locators_version` mismatch: 409 `element changed, review again`.

  `HealResponse` holds `id`, `element_id`, `key`, `status`, `method`, `confidence`, `old_locators`, `new_locators`, `reason`, `evidence_source`, `execution_id`, `test_case_id`, `created_at`, `decided_by`, and `decided_at`. `ElementResponse.latest_heal` is the element's heal with the latest `created_at`, or null.
  - **Script heal routes**: these live under the existing test case prefix `/api/v1/projects/{project_id}/testcases` (no hyphen, the same prefix every other test case route uses):
    - `GET /script-heals?status=`
    - `POST /script-heals/{heal_id}/accept`, `/reject`, and `/revert`

    They are registered in `backend/main.py` before the `/testcases/{test_case_id}` routes, so `script-heals` is never parsed as a test case id. They use the same status rules and errors, with these exact cases:
    - 404 `Script heal not found`: an unknown heal, one in another project, or one whose test case is missing or soft deleted. The list route returns 404 `Project not found` for an unknown project.
    - 409 `heal is <status>`: the heal is in the wrong status for the action.
    - 409 `script changed, review again`: the stored script no longer equals the heal's `old_script` (accept) or `new_script` (revert) (AC-21).
    - 422: an unknown `status` value on the list route. `ScriptHealResponse` holds `id`, `test_case_id`, `status`, `old_script`, `new_script`, `rca_category`, `root_cause_summary`, `failing_step`, `reason`, `execution_id`, `created_at`, `decided_by`, and `decided_at`. `TestCaseResponse.latest_script_heal` is the test case's latest script heal, or null.
- **AC-13 (permissions and audit)**: Listing heals needs `ObjectRepository: view`. Accept, reject, and revert need `ObjectRepository: edit`. Without the permission, the route returns 403.
  - Each accept, reject, revert, and automatic apply writes one audit entry through `AuditService`: entity `object_locator_heal`, action `heal_applied | heal_accepted | heal_rejected | heal_reverted`, heal id, element key, and old and new main locator. The user is null for automatic ones.
  - `self_healing_enabled` changes through the existing project PATCH and its existing permission.
  - **Script heals**: listing needs `TestCases: view`, and accept, reject, and revert need `TestCases: edit`. Audit entries go through `log_audit` with entity type `TESTCASE` and action `script_heal_applied | script_heal_accepted | script_heal_rejected | script_heal_reverted`. The changes payload carries `entity: "test_case_script_heal"`, the heal id, and the test case id. The script text is never put in the audit entry.
- **AC-14 (UI)**: The Object Repository view shows:
  - a `Healed` badge (latest heal `applied` or `accepted`) and a `Suggestion` badge (`suggested`) next to the health badge, as text and not colour alone
  - the number of pending suggestions in the attention card
  - a `Healing` section in the element drawer with old and new locators, confidence, method, reason, and Accept, Reject, or Revert buttons that only users with `edit` see

  The test case view shows:
  - a `Script repaired` badge (latest script heal `applied` or `accepted`) or a `Script suggestion` badge (`suggested`), as text
  - a `Script healing` section with the RCA category and root cause summary, a side by side old and new script diff, and Accept, Reject, or Revert buttons that only users with edit see

  It shows empty, loading, and error states and is usable with a keyboard. The project settings form has a `Self healing` switch, which covers both stages. It reads `self_healing_enabled` from the project response and saves it through the project PATCH.
- **AC-15 (never breaks a run)**: `LocatorHealingService` and `SelfHealingAgent` never raise. Any error is logged, saves nothing for that target, and the graph continues to `bug_reporting_agent`. Apart from the rerun, healing never changes a result's status.
- **AC-16 (retention)**: Each element keeps all its `suggested` heals and its latest `applied` or `accepted` heal (the revert source). Beyond those, it keeps the last 10 decided heals, oldest trimmed first.
- **AC-17 (parity)**: Everything above behaves the same on the JSON store and on PostgreSQL. After any SQLAlchemy error, the store rolls back and clears the session before the next operation.
  - AC-15, AC-16, and AC-17 also cover script repair. `ScriptRepairService` never raises. Each test case keeps its `suggested` script heal, its latest `applied` or `accepted` one, and the last 10 decided ones. `ScriptHeal` behaves the same on both backends of `ScriptHealStore` (JSON file and PostgreSQL table, see the data model).

**Script repair (amendment 2026-11-26)**:
- **AC-18 (script repair trigger)**: `route_after_execution_analysis` sends the graph to `self_healing_agent` for stage 2 only when all of these hold for the latest result of a test case:
  - `healing_enabled` is true and `object_repository.healing.script_repair.enabled` is true
  - the status is `failed` or `error`
  - `failure_category` is `Flaky Test / Automation Issue` and `confidence >= script_repair.min_rca_confidence` (0.6)
  - every `[OBJREPO]` marker for that result is `ok: true`, or each `ok: false` key has a heal that resolved in this rerun. A remaining locator failure is a stage 1 problem, so it is never sent to script repair.
  - the test case has a `playwright_script` and the project framework is Playwright
  - `test_data_sets` is empty. Data driven templates are a follow-up.
  - `script_repair_attempts < script_repair.max_attempts` (1)

  A first run with no `ok: false` markers that meets these rules goes straight to stage 2. `Product Bug` and `Test Environment Issue` never trigger a script change.
- **AC-19 (script repair inputs)**: `ScriptRepairService` regenerates the script with `PlaywrightAgent.run` and `state.repair_feedback`, the same path that `GroundedPlaywrightService._validate_and_run_case` and `WorkflowService` script generation use.
  - **Feedback**: built from the latest result's `failure_category`, `root_cause_summary`, `failing_step`, `trace_analysis`, and up to 8 `evidence` lines, capped at 2000 characters. Log and page text is wrapped in delimiters and marked as untrusted data. The prompt tells the model never to follow instructions found inside it, to keep the test's intent and `expected_result`, and to use only object repository keys.
  - **Page evidence**: `dom_context` is built from the failed run's `harvest.json` (`read_harvest_file`, with the AC-2 redaction).
  - **Object repository**: the prompt lists the current repository, including any locator heal applied in stage 1, so the new script uses the healed keys.
  - **Never sent**: credentials, typed values, and test data. Vault references in the script stay as they are.
  - An LLM error saves nothing, and the original failure goes on to bug reporting.
- **AC-20 (script fix gates)**: a repaired script is applied only when all of these hold:
  - `validate_playwright_script(rendered, dom_context, expected_result)` returns no issues, run on the script rendered with `ObjectRepositoryService.render_for_test_case`
  - `ObjectRepositoryService.unknown_keys` is empty
  - the new script has at least as many `expect(` assertions as the old one, so a fix can't weaken the test into passing
  - the script differs from the old one
  - `expected_result`, steps, and test data are unchanged. Only `playwright_script` changes.

  When a gate fails, the fix is saved as `suggested` with the gate issues in `reason`, there is no rerun, and the original failure goes on to bug reporting. If the TypeScript validator is unavailable, nothing is saved.
- **AC-21 (script apply, rerun, and revert)**:
  - **Apply**: write a `ScriptHeal` row with status `applied`, `old_script`, and `new_script`, then save `test_case.playwright_script` through the project repository. Set `script_validation_status = UNVERIFIED` with the diagnostic `Repaired by self healing`, and write one audit entry (AC-13).
  - **Race check**: at apply, accept, and revert, the stored script is compared with the heal's `old_script` (or `new_script` for revert). A mismatch on the automatic apply makes it a suggestion. On the routes it returns 409 `script changed, review again`.
  - **Rerun**: `self_healing_agent` sets `heal_rerun_test_case_ids`, adds 1 to `script_repair_attempts`, and routes to `execution_agent`. That uses the AC-9 rules: only that test case, retries off, the same `execution_batch_id`, `heal_rerun_of` set to the failed result, and `heal_ids` including the script heal id.
  - **Revert** (a failed stage 2 rerun, AC-10): restore `old_script` when the stored script still equals `new_script`. The heal becomes `suggested`. If a tester changed the script meanwhile, the script is left alone and a warning is logged.
  - **Accept a suggestion**: save `new_script` with the race check against `old_script`, and set `accepted`. Nothing is rerun.
  - An element has at most one `suggested` heal (AC-8), and a test case has at most one `suggested` script heal. A newer one rejects the older one with `reason = "replaced by a newer suggestion"`.

## Decision

**Chosen option**: Option 2: a post run healing node that heals from the run's harvest, applies confident matches to the repository, and reruns once.

A new `self_healing_agent` graph node sits between `execution_analysis_agent` and `bug_reporting_agent`. It uses a new `LocatorHealingService`, which scores deterministically first and uses the LLM as a picker only. It writes `LocatorHeal` records through the object repository store, and reruns the healed test case once through `execution_agent` with retries off.

**Amendment (2026-11-26)**: the same node gains a second stage. When the test still fails with every locator resolving and the RCA says automation issue, a new `ScriptRepairService` regenerates the script through `PlaywrightAgent`'s existing repair feedback path. It applies the script only when the gates pass, reruns once more, and restores the script if that rerun fails. There are at most 2 reruns per run.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

**Data model sketch**:
- **LocatorHeal** (new). PostgreSQL table `object_locator_heals`; in the JSON store it is a `heals` list nested under each element.
  - `id` UUID PK
  - `element_id` UUID FK → `object_elements.id`, cascade delete, required
  - `project_id` UUID required
  - `execution_id` UUID null, `test_case_id` UUID null
  - `status` enum `applied | suggested | accepted | rejected | reverted`, required
  - `method` enum `deterministic | llm`, required
  - `confidence` float from 0 to 1, required
  - `old_locators` JSON and `new_locators` JSON, both required, each a list of `{strategy, value, options}`
  - `old_fingerprint` text null, `candidate_fingerprint` text null
  - `reason` text required
  - `evidence_source` text required (`harvest` in this slice)
  - `locators_version` int required (the element's version when the heal was computed or applied)
  - `created_at` required, `decided_by` UUID null, `decided_at` null
  - Indexes on `(element_id, created_at)` and `(project_id, status)`. A partial unique index on `(element_id) WHERE status = 'suggested'`.
- **ScriptHeal** (new, amendment 2026-11-26). It lives in its own store, `ScriptHealStore` (`backend/repository/script_heal_store.py`), never inside the test case record, so no test case save can drop or overwrite it.
  - **Backends**: `JsonScriptHealStore` writes `backend/database/script_heal_store.json` (top level key `script_heals`, keyed by heal id) under one process lock, through a temp file and an atomic rename. `PostgresScriptHealStore` uses the table `test_case_script_heals`, one short lived session per call, rolling back and closing on any error. `InMemoryScriptHealStore` serves tests.
  - **Selection**: `get_script_heal_store()` follows `repository.provider`, the same setting as `get_project_repository()`. `set_script_heal_store()` swaps it in tests.
  - **Writes**: every change to one test case's heals goes through `mutate(test_case_id, fn)`, so the single suggestion rule and retention (`trim_script_heals`) apply in one read and write. A PostgreSQL `IntegrityError` on the partial unique index becomes `ScriptHealIntegrityError`.
  - **Fields**:
    - `id` UUID PK
    - `test_case_id` UUID FK → `test_cases.id`, cascade delete (PostgreSQL only; it fires only on a hard delete, and test case delete is soft, see Consequences), required
    - `project_id` UUID required
    - `execution_id` UUID null
    - `status` enum `applied | suggested | accepted | rejected | reverted`, required
    - `old_script` text and `new_script` text, both required, holding object repository keys only (never a rendered script)
    - `rca_category` text required, `root_cause_summary` text required (default empty), `failing_step` text null
    - `reason` text required (default empty; why it was applied or suggested, including any gate issues)
    - `created_at` required, `decided_by` UUID null, `decided_at` null
  - Indexes on `(test_case_id, created_at)` and `(project_id, status)`. A partial unique index `uq_test_case_script_heals_one_suggested` on `(test_case_id) WHERE status = 'suggested'`.
- **ObjectElement**: new `locators_version` int, not null, server default 0. `heals` loads with the element inside the store unit.
- **Project**: `self_healing_enabled` boolean, not null, server default `true`.
- **ExecutionResult** (`executions` table): `heal_rerun_of` UUID null, and `heal_ids` JSON list of UUID, null.
- **WorkflowState** (runtime only, never persisted):
  - `healing_enabled: bool = False` (set by `WorkflowService`)
  - `execution_batch_id: str | None` (set by ExecutionAgent on its first run)
  - `heal_attempts: int = 0`
  - `heal_results: list[dict]`, each with `heal_id`, `key`, `status`, `confidence`, `test_case_ids`, `locators_version`
  - `heal_rerun_test_case_ids: list[UUID]`
  - `run_marker_lines: dict[str, list[str]]` (`[OBJREPO]` lines keyed by result id)
  - `script_repair_attempts: int = 0` (amendment)
  - `script_heal_results: list[dict]`, each with `script_heal_id`, `test_case_id`, `status`, `rca_category`, and `verification` (amendment)
  - A `heal_results` entry may carry `verification: "pending_script_repair" | "passed" | "failed"` (AC-10)

**Marker capture**: ExecutionAgent wraps its runner in a thin proxy. While each runner call is in flight, `[OBJREPO]` lines from `on_log` are tagged with that call's `run_id` (the test case id). After each `ExecutionResult` is built, ExecutionAgent writes that test case's lines (all attempts) to `state.run_marker_lines[str(result.id)]`. Calls run one after another, so the tagging is unambiguous. `WorkflowService` stops collecting its own `marker_lines` and reads `run_marker_lines` from the final state. Nothing reads `ExecutionResult._raw_payload`, which is not a declared field and is not guaranteed to survive between graph nodes.

**State transitions** (LocatorHeal.status):
- `applied` → `suggested` (rerun failed, AC-10), or `reverted` (tester, AC-12)
- `suggested` → `accepted` (tester), or `rejected` (tester, or replaced by a newer suggestion)
- `accepted` → `reverted` (tester)
- `rejected` and `reverted` are final.
- An `applied` heal with `verification: "pending_script_repair"` stays `applied` until the stage 2 rerun decides it (AC-10).
- ScriptHeal.status follows the same transitions. `applied` becomes `suggested` when the stage 2 rerun fails.

**Graph** (`backend/graph/workflow.py`):
```
execution_agent → [retrieval_analysis] → execution_analysis_agent
  → route_after_execution_analysis: self_healing_agent | bug_reporting_agent
self_healing_agent
  → route_after_self_healing: execution_agent (heal_rerun_test_case_ids not empty) | bug_reporting_agent
```
- `route_after_execution_analysis` is a pure function of the state. It sends the graph to `self_healing_agent` when `healing_enabled` is true and either:
  - `heal_attempts < max_attempts_per_run` and the latest failed or error result has an `ok: false` marker in `run_marker_lines`, or
  - an `applied` heal in `heal_results` waits for verification.
- The fixed edge `execution_analysis_agent → bug_reporting_agent` is replaced.
- **Amendment (2026-11-26)**: `route_after_execution_analysis` also sends the graph to `self_healing_agent` when AC-18 holds, or when an applied script heal waits for verification. Inside the node the phases are checked in this order:
  1. verify (an applied heal or script fix waits for its rerun)
  2. stage 2 script repair (AC-18 holds)
  3. stage 1 locator heal
  `route_after_self_healing` is unchanged. Two stages give this flow:
```
run 1 fails (ok:false markers) → stage 1 heal → rerun 1
  → passed: done | healed key failed again: revert heal, done
  → key resolved, still fails, RCA = automation issue → stage 2 script repair → rerun 2
      → passed: keep both | failed: restore script, revert pending heal, done
run 1 fails (all markers ok, RCA = automation issue) → stage 2 script repair → rerun 1 → verify
all passed, or no target and no trigger → bug_reporting_agent (nothing processed)
```
- `self_healing_agent` is a new `build_graph(...)` parameter, defaulting like the others, and a new `routing_map` entry. Its constructor takes `project_id`, `repo`, and an optional `LocatorHealingService`, like `ExecutionAgent`.

**API surface** (prefix `/api/v1/projects/{project_id}/object-repository`):

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| /heals | GET | status (query, optional) | list of HealResponse, newest first | ObjectRepository view | 403 |
| /heals/{heal_id}/accept | POST | none | HealResponse, ElementResponse | ObjectRepository edit | 403, 404, 409 |
| /heals/{heal_id}/reject | POST | none | HealResponse | ObjectRepository edit | 403, 404, 409 |
| /heals/{heal_id}/revert | POST | none | HealResponse, ElementResponse | ObjectRepository edit | 403, 404, 409 |
| /api/v1/projects/{project_id}/testcases/script-heals | GET | status (query, optional) | list of ScriptHealResponse, newest first | TestCases view | 403, 404 unknown project, 422 bad status |
| .../testcases/script-heals/{heal_id}/accept | POST | none | ScriptHealResponse | TestCases edit | 403, 404, 409 |
| .../testcases/script-heals/{heal_id}/reject | POST | none | ScriptHealResponse | TestCases edit | 403, 404, 409 |
| .../testcases/script-heals/{heal_id}/revert | POST | none | ScriptHealResponse | TestCases edit | 403, 404, 409 |
| /api/v1/projects/{id} | PATCH (existing) | self_healing_enabled: bool (optional) | project with `self_healing_enabled` | existing | existing |

**Value sourcing**:

| Action | Value produced or displayed | Source |
|---|---|---|
| Route | healing enabled | `state.healing_enabled`, set by `WorkflowService` from settings and `project.self_healing_enabled` |
| Detect target | failing key | `state.run_marker_lines[str(result.id)]`, parsed by `ObjectRepositoryService.parse_markers` |
| Detect target | element and its page | `ProjectObjectRepository.element_by_key(key)` |
| Evidence | candidates | `read_harvest_file(ARTIFACTS_ROOT / test_case_id)`, filtered by the page `url_pattern` glob, then redacted |
| Score | expected signals | the element's stored locators through the AC-4 strategy table, plus `element_type` and `fingerprint` |
| Decide | thresholds and margin | `object_repository.healing` config |
| Decide | final confidence | the deterministic score, or `min(clamp(llm.confidence), score + 0.2)` |
| Apply | new locators and fingerprint | candidate locators through `order_recon_locators`, and `ObjectRepositoryService.fingerprint(candidate)` |
| Apply | version to check | the element's `locators_version`, read after the failed run's markers are applied |
| Rerun | which test cases | `heal_rerun_test_case_ids` |
| Rerun | batch id | `state.execution_batch_id` |
| Rerun | `heal_rerun_of` | the id of the latest failed result for that test case before the rerun |
| Verify | rerun outcome | the latest result for each test case in that heal's `test_case_ids` |
| Report | `Passed after heal` | latest result passed, and its `heal_rerun_of` is not null |
| API accept | `decided_by` | the authenticated user from the route dependency |
| UI | badges | `ElementResponse.latest_heal.status` |
| UI | switch value | `self_healing_enabled` in the project response |
| Script repair trigger | RCA category and confidence | the latest result's `failure_category` and `confidence`, set by `LogAnalysisService` in `execution_analysis_agent` |
| Script repair trigger | locators resolved | `state.run_marker_lines[str(latest_result.id)]`, every marker `ok: true` or healed |
| Script repair | repair feedback | the latest result's `root_cause_summary`, `failing_step`, `trace_analysis`, and `evidence` |
| Script repair | page evidence | `read_harvest_file(ARTIFACTS_ROOT / test_case_id)`, redacted, as `dom_context` |
| Script repair | keys in the prompt | the current object repository, after any stage 1 apply |
| Script gates | issues | `validate_playwright_script` on `render_for_test_case`, plus `unknown_keys` and the `expect(` count compared with the old script |
| Script apply | old script | `test_case.playwright_script` read just before the apply |
| Script rerun | which test case | `heal_rerun_test_case_ids` |
| Script revert | script to restore | `ScriptHeal.old_script`, applied only when the stored script still equals `new_script` |
| UI | script badges | `TestCaseResponse.latest_script_heal.status` |
| API | `latest_script_heal` | the test case's heal with the newest `created_at` from `ScriptHealStore` (list responses read the project's heals once). A store read failure logs a warning and returns null, never an error |

**Key invariants**:
- Heals change locators only through `LocatorHealingService`, inside one `ObjectRepositoryStore.mutate`. A locator heal never touches a stored test case script.
- Scripts change only through `ScriptRepairService`, only after an automation issue RCA, and only when the AC-20 gates pass. A script whose rerun fails is restored. Only `playwright_script` changes, never the steps, the expected result, or the test data.
- A `Product Bug` or `Test Environment Issue` RCA never changes a script.
- Script heals are read and written only through `ScriptHealStore.mutate`, never stored on the test case record.
- No more than 2 reruns per graph run: one for stage 1 and one for stage 2.
- A passed run is never processed by healing.
- The LLM only selects among candidates and never produces a locator string. Page text in the prompt is untrusted data.
- An element with `manually_edited = true` is never auto applied.
- At most one `suggested` heal per element.
- `locators_version` changes only on tester edits, heals, accepts, and reverts.
- No more than `max_attempts_per_run` heal attempts and reruns per graph run. A heal rerun never uses `RecoveryManager` retries.
- No credential, typed value, or test data reaches a heal record or the LLM prompt.

**Security model**:
- Heals are project scoped, and every route checks that the heal's `project_id` matches the path.
- View and edit follow the `ObjectRepository` permission (AC-13).
- No new compliance scope, and no new browser session beyond the one rerun.

**Configuration required** (`backend/config/default_config.yaml`, `ObjectRepositoryConfig.healing` in `backend/config/settings.py`; no new env vars):
- `object_repository.healing.enabled: true`
- `object_repository.healing.auto_apply_threshold: 0.85`
- `object_repository.healing.suggest_threshold: 0.5`
- `object_repository.healing.min_margin: 0.15`
- `object_repository.healing.llm_enabled: true` (uses the `analysis` LLM role, `agent="self_healing_agent"`)
- `object_repository.healing.max_attempts_per_run: 1`
- `object_repository.healing.max_candidates_for_llm: 5`
- `object_repository.healing.script_repair.enabled: true` (amendment)
- `object_repository.healing.script_repair.min_rca_confidence: 0.6`
- `object_repository.healing.script_repair.max_attempts: 1`
- Script repair uses the LLM role `PlaywrightAgent` already uses for script generation. The per project `self_healing_enabled` switch covers both stages.

**Critical test scenarios**:
- Happy path: a button's label changes but its test id stays the same. The heal is applied, the test case is rerun once with retries off and passes, no bug is filed, the report says `Passed after heal`, the failed attempt is left out of run history, and the markers leave the element `healthy`. Verifies **AC-1**, **AC-4**, **AC-5**, **AC-7**, **AC-9**, **AC-11**.
- Same fingerprint, stale css: a candidate with the element's own fingerprint scores 1.0 and is applied. Verifies **AC-4**.
- Graph round trip: `run_marker_lines`, `heal_results`, and the heal fields on results survive between nodes. Verifies **AC-1**, **AC-9**.
- Unclear match: two similar buttons. The StubLLM picks index 1, confidence is clamped, the final confidence is at most score + 0.2, and the heal becomes a suggestion with no rerun. Verifies **AC-5**, **AC-6**, **AC-8**.
- LLM failure: the heal becomes a suggestion when top >= 0.5. Verifies **AC-6**.
- No harvest for the page: no heal and the log line is written. Verifies **AC-2**.
- Rerun fails: the heal is reverted to `suggested`, the old locators are back, and a later harvest ingest doesn't create a duplicate. Verifies **AC-8**, **AC-10**.
- Version check: markers alone don't trip the version check, but a tester edit between computing and applying does. The apply becomes a suggestion, and an accept returns 409. Verifies **AC-7**, **AC-12**.
- Evidence kept: the failed run's trace and screenshot paths still exist after the rerun, and a missing file is skipped. Verifies **AC-3**.
- Healing off globally or per project, or an environment issue: no heal and a straight route to bug reporting. Verifies **AC-1**.
- A passed result with an `ok: false` marker produces no target. Verifies **AC-1**.
- Service throws: the graph still reaches the report. Verifies **AC-15**.
- Permissions: a viewer gets 403 on accept, reject, and revert. Verifies **AC-13**.
- Retention: 12 decided heals are trimmed to 10, keeping the suggested one and the latest applied one. Verifies **AC-16**.
- Parity: apply, accept, revert, and the suggestion race (`IntegrityError` dropped) on the JSON store and on PostgreSQL. Verifies **AC-8**, **AC-17**.
- All passed: no heal phase and no script repair, and nothing saved. Verifies **AC-1**.
- Stage 1 rerun passes: the heal is kept and stage 2 never runs. Verifies **AC-10**.
- Two stages: a renamed button plus a changed step. Stage 1 heals the key, the rerun resolves it but fails later, and the RCA (StubLLM) says automation issue. Stage 2 repairs the script with the healed key in the prompt, and the second rerun passes. Both are kept, and the report says `Passed after heal` with both rows. Verifies **AC-10**, **AC-11**, **AC-18**, **AC-19**, **AC-21**.
- Script only: no `ok: false` markers and an automation issue RCA go straight to stage 2 with one rerun. Verifies **AC-18**.
- Stage 2 rerun fails: the old script is back, the script heal and the pending locator heal are `suggested`, and no third rerun runs. Verifies **AC-10**, **AC-21**.
- A Product Bug RCA, a Test Environment Issue RCA, or confidence under 0.6: no script change, and a bug report is filed for the Product Bug. Verifies **AC-18**.
- The healed key fails again in the rerun: the heal is reverted and no script repair runs. Verifies **AC-10**.
- Gates: a fix with fewer `expect(` calls, an unknown key, or a validator issue becomes a suggestion with no rerun. Verifies **AC-20**.
- The repair prompt holds no credential or test data value, and page text sits inside the untrusted data delimiters. Verifies **AC-19**.
- A data driven test case (`test_data_sets` not empty) is skipped. Verifies **AC-18**.
- A tester changes the script between compute and apply: it becomes a suggestion, and accept returns 409 `script changed, review again`. Verifies **AC-21**, **AC-12**.
- `ScriptRepairService` throws: the graph still reaches the report. Verifies **AC-15**.

## Build plan

Ordered as Tracer Bullet: a thin end to end heal first (one deterministic heal from harvest, applied, rerun, reported), then thickened with the LLM, suggestions, revert, the API, and the UI.

1. Write the Alembic migration (revises head `c3f8a1d7e925`):
   - add the `object_locator_heals` table
   - add `object_elements.locators_version`, `projects.self_healing_enabled`, `executions.heal_rerun_of`, and `executions.heal_ids`

   Add the ORM models in `backend/database/db_models.py`. Satisfies **AC-7**, **AC-11**, **AC-17**.
2. Domain models and store mappings:
   - Add `LocatorHeal` and `ObjectElement.locators_version` to `backend/models/object_repository.py`, `self_healing_enabled` to `backend/models/project.py`, the heal fields to `ExecutionResult`, and the runtime fields to `WorkflowState`.
   - Map them in the JSON store, the Postgres object repository store, the Postgres project repository, and the executions mapping.
   - Make `create_element` and `update_element` add 1 to `locators_version`.

   Satisfies **AC-7**, **AC-11**, **AC-17**.
3. Add the `healing` config to `default_config.yaml` and `ObjectRepositoryConfig`. Have `WorkflowService` set `state.healing_enabled`, satisfies **AC-1**, **AC-5**.
4. ExecutionAgent:
   - add the runner proxy that writes `run_marker_lines`
   - set `execution_batch_id`
   - run only `heal_rerun_test_case_ids` when it is set, with retries off and the same batch id, then clear it
   - set `heal_rerun_of` and `heal_ids`

   Make `ExecutionAnalysisAgent` analyse only results whose `failure_category` is null. Satisfies **AC-1**, **AC-9**.
5. Build `backend/services/object_repository_healing.py`:
   - target detection from `run_marker_lines`
   - harvest evidence with redaction
   - deterministic scoring with the AC-4 table
   - the AC-7 order (markers, version read, score, apply in one `mutate` with the fingerprint move)
   - the evidence copy into `heal-<attempt>/`

   Satisfies **AC-1**, **AC-2**, **AC-3**, **AC-4**, **AC-5**, **AC-7**, **AC-15**.
6. Add `backend/agents/self_healing_agent.py` (heal phase, then verify phase) and its prompt folder `backend/prompts/agents/self_healing/`. Map it in `backend/utils/prompts.py`, satisfies **AC-1**, **AC-9**, **AC-15**.
7. Graph and supervisor:
   - add `route_after_execution_analysis` and `route_after_self_healing`
   - add the `build_graph` parameter, the node, and the routing map entry
   - replace the fixed edge

   Satisfies **AC-1**, **AC-9**.
8. Latest result everywhere:
   - BugReportingAgent, ReportAgent (`Passed after heal` and the `Self healing` section), `langsmith_tracer.py`, and `run_history` (leave out superseded attempts)
   - `WorkflowService` EXECUTE: save every result, give `execution_id` to the latest, apply the rerun's markers from `run_marker_lines`, harvest from the run directory, and run indexing, Jira sync, and reports for the latest only

   Satisfies **AC-11**.
9. Write `backend/tests/test_self_healing.py` for the tracer: the happy path graph test with `FakePlaywrightRunner` (a failure with an `ok: false` marker and a `harvest.json`, then a pass for the same `run_id`), plus the graph round trip test, satisfies **AC-1**, **AC-7**, **AC-9**, **AC-11**.
10. Add the LLM fallback with `HealSelectionResponse` (clamping, untrusted text delimiters) and the suggestion path:
    - the single pending suggestion rule, in one `mutate`, with Postgres `IntegrityError` dropped
    - the `ingest_recon` skip for pending `candidate_fingerprint`

    Add their tests. Satisfies **AC-5**, **AC-6**, **AC-8**.
11. Add the rerun failure revert (verify phase) and its test, satisfies **AC-10**.
12. Add the API routes and DTOs (`HealResponse`, `ElementResponse.latest_heal`), the service methods `list_heals`, `accept_heal`, `reject_heal`, and `revert_heal`, the audit entries, and the API and permission tests, satisfies **AC-12**, **AC-13**.
13. Add retention trimming and its test, plus the JSON and Postgres parity tests, satisfies **AC-16**, **AC-17**.
14. Frontend:
    - `api.js` heal calls
    - `object-repository.js` badges, the attention count, and the drawer Healing section
    - `styles.css` `.objrepo-heal-*`
    - the project settings `Self healing` switch in `app.js`

    Extend `backend/e2e/object-repository.spec.ts` to accept a suggestion. Satisfies **AC-14**.
15. Docs: `docs/features/object-repository.md`, `docs/workflow.md`, `docs/agents.md`, `docs/api.md`, and a new gotcha line in `backend/services/AGENTS.md` (heals change locators only through `LocatorHealingService`, inside `mutate`; `locators_version` changes only on edits and heals), satisfies **AC-1** to **AC-17** (documentation rule in `AGENTS.md`).

**Script repair build steps (amendment 2026-11-26)**, after steps 1 to 15:

16. Write an Alembic migration for `test_case_script_heals` (revises `e1a9c4b7d203`), and add the ORM model, the `ScriptHeal` domain model, the `ScriptHealStore` interface with its JSON, PostgreSQL, and in memory backends, the `script_repair` config, and the `WorkflowState` fields. Satisfies **AC-17**, **AC-21**.
17. Build `backend/services/script_repair_service.py` with:
    - trigger checks (AC-18)
    - feedback and `dom_context` from the harvest
    - a `PlaywrightAgent` call with `repair_feedback`
    - the gates
    - apply, the race check, revert, and the single suggestion rule

    Satisfies **AC-18** to **AC-21**, **AC-15**.
18. In `SelfHealingAgent`, add the stage 2 phase and the two stage verify rules. In `supervisor_agent.route_after_execution_analysis`, add the stage 2 and script verify routes. Satisfies **AC-1**, **AC-10**, **AC-18**.
19. Add script heals to the report's `Self healing` section, extend `run_history` to leave out up to 2 superseded attempts, and log script heals in `WorkflowService`. Satisfies **AC-11**.
20. Add the script heal API routes, DTOs, `TestCaseResponse.latest_script_heal`, audit entries, and permissions, then the test case view badges and the `Script healing` drawer section with the diff. Satisfies **AC-12** to **AC-14**.
21. Add the script repair scenarios to `backend/tests/test_self_healing.py` and an e2e accept test. Update `docs/workflow.md`, `docs/agents.md`, `docs/api.md`, and `backend/services/AGENTS.md` with the gotcha: scripts change only through `ScriptRepairService`, never on a Product Bug RCA. Satisfies **AC-1** to **AC-21**.

## Consequences

**Positive**:
- A renamed label, a changed test id, or stale css no longer fails every run until someone edits the repository. One heal fixes every script that uses the key.
- The fixed rules handle the common cases with no LLM cost. The LLM can only choose among candidates already seen on the page.
- Failure evidence is kept even though the rerun reuses the run directory.

**Negative / tradeoffs**:
- A healed test case runs twice. The rerun has retries off, but a failing case can still take up to twice as long plus the first run's retries.
- A rerun that passes for an unrelated reason (a flaky test) leaves a wrong heal applied until a tester reverts it or a later run fails it. The rerun is strong evidence, not proof.
- Every consumer must use the latest result per test case. Any code still reading `execution_results[0]` will be wrong.
- Pages the run didn't harvest (beyond `harvest_max_pages`, or popups) can't be healed in this slice.
- The fixed weights are guesses at first and will need tuning against real heals.
- A candidate that is already its own element in the repository is excluded, so that case is not healed.
- `ingest_recon` now also checks pending suggestions on each page it ingests. That is a small extra cost per ingest.
- With script repair, a test case can run up to three times (the first run, stage 1, and stage 2), plus the first run's retries.
- An LLM rewritten script could hide a real defect. This is mitigated by:
  - the automation issue RCA gate
  - the confidence floor
  - the assertion count and expected result gates
  - the restore when the rerun fails
  - the visible `Script repaired` badge

  The RCA itself can be wrong, so a misread Product Bug could still be "repaired".
- A locator heal whose key resolved but whose test failed later now stays applied a little longer, until stage 2 decides it.
- The script save (project repository) and the heal record (`ScriptHealStore`) are two separate transactions, and the build saves the script first. A crash or store error between them leaves a repaired script with no heal record, so nothing can revert it and no badge shows. The same order applies to accept and revert.
- A `script_heal_store.json` that can't be parsed reads as empty, so the next `mutate` overwrites it and every script heal is lost.
- Test case delete is a soft delete (`is_deleted`) on both backends, so the PostgreSQL cascade never fires and the JSON store has no foreign key. A deleted test case's script heals stay in the store and still show in `GET /testcases/script-heals`, while accept, reject, and revert on them return 404 `Script heal not found`.

**Neutral**:
- One additive migration with nullable or defaulted columns, so rollback is a downgrade.
- The batch and webhook execution paths (`ExecutionService.finalize_execution`) don't heal in this slice.
- An applied heal leaves `needs_review` until the rerun's markers set `healthy`. The `Healed` badge comes from the heal row, so it stays visible.

## Follow-up

- [ ] Add aria snapshot evidence (suggest only: role and name candidates can't reach the auto apply bar and have no fingerprint).
- [ ] Add live inspect evidence. It needs the run's saved login session, because the scan inspector never logs in and would see the login page.
- [ ] Heal on the batch and webhook paths, possibly suggest only with no rerun.
- [ ] Merge a healed element with a duplicate element that already holds the candidate's fingerprint, instead of excluding that candidate.
- [ ] Tune the score weights and thresholds from the accepted and rejected heal history.
- [ ] Consider a time cap on heal and rerun per execution.
- [ ] Healing during the run inside `orx()`, if post run healing proves too slow.
- [ ] Selenium scripts.
- [ ] Decide whether to delete the unused `LocatorResolver` (still open from spec 0004).
- [ ] Script repair for data driven templates (`test_data_sets`).
- [ ] Script repair and locator heals on the batch and webhook paths, and an option to rerun the whole suite after healing.
- [ ] Tune `min_rca_confidence` from the accepted and rejected script heal history.
- [ ] Hide or clean up script heals whose test case is soft deleted (both backends), so the list route stops showing heals no one can decide.
- [x] Write the heal record before the script on apply (for example as `applied`, then mark it `suggested` if the script save fails), so a crash between the two never leaves a script change with no record.
- [x] Make `JsonScriptHealStore` raise on a file it can't parse instead of reading it as empty, so a corrupt file is never overwritten.
- [x] Add `backend/database/script_heal_store.json` to `.gitignore`, like the other local JSON stores (the project rule keeps local databases out of source).