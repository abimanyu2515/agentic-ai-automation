# 0011. Rationale: self healing locators

## Context

The object repository (spec 0004) stores 1 to 5 ordered locators per element. At run time `orx()` tries them in order and logs one `[OBJREPO]` marker per lookup. `ObjectRepositoryService.apply_run_markers` turns those markers into health: `needs_review` after a failed run, `broken` after two in a row. Nothing repairs a locator. When every locator fails, the test fails and stays failed until a tester edits the element or a scan happens to refresh it. Meanwhile the log analyser classifies the failure as `Flaky Test / Automation Issue`, so it is noise rather than a finding.

The system already captures most of what a repair needs: the harvest of every page a run visits (`harvest.json`), the aria snapshot Playwright writes on failure, and a scan inspector for safe test environments. Scripts hold only element keys, so a repaired locator reaches every script on the next run with no regeneration.

The forces are:
- A wrong repair is worse than no repair, because it can make a test pass against the wrong element.
- LLM calls cost money and are not repeatable.
- A run's outcome must not depend on side work.
- The JSON store and PostgreSQL must behave the same.
- The rerun reuses the same artifact directory, so failure evidence is lost unless it is copied first.

## Options considered

### Option 1: Heal inside `orx()` during the run

When every locator fails, harvest the live page in the browser and match it by fingerprint or attributes on the spot.

**Pros**:
- No rerun is needed, so it's fast.
- The test continues in the same session.

**Cons**:
- The logic lives in injected JavaScript that must stay in step with three strategy mappings.
- There is no LLM and no human review.
- A wrong match passes silently inside the run.

### Option 2: Heal after the run, then rerun once (chosen)

A graph node after the log analyser heals from captured evidence, applies confident matches, saves unsure ones as suggestions, and reruns the healed test cases once.

**Pros**:
- It fits the LangGraph state contract and dependency injection.
- It can use the LLM and human review.
- The rerun proves the heal, and a failed rerun undoes it.

**Cons**:
- A second run costs time.
- Several results per test case mean consumers must pick the latest.

### Option 3: Suggest only, never auto apply

Compute heals after the run, but always wait for a tester.

**Pros**:
- No risk of a wrong automatic change.

**Cons**:
- The failing run stays failed, and every run fails until someone acts, which is most of today's pain.

### Option 4: LLM only matching

Always send the aria snapshot to the LLM and let it name the element.

**Pros**:
- Less code.
- Handles odd changes.

**Cons**:
- It costs money on every heal and gives different answers on the same input.
- It may invent locators that aren't on the page.

## Rationale

Option 2 tackles the main pain (one UI change failing every run) while respecting the strongest force (a wrong repair is worse than none). The rerun turns every automatic heal into a checked claim. A failed rerun sends it back to a tester as a suggestion. A rerun that passes only because the test is flaky can still leave a wrong heal in place, so the rerun is strong evidence, not proof, and the `Healed` badge keeps every heal visible for review. Scoring with fixed rules first keeps the common cases (renamed label, same test id) cheap and repeatable. The LLM is limited to choosing among real candidates already seen on the page, so it can't invent a locator. Healing after the run keeps the logic in Python services with injected dependencies, so it is testable with `StubLLMService` and `FakePlaywrightRunner`. Option 1 would put it in injected JavaScript with no review path.

Several decisions were settled during the design conversation, each against a runner up:
- **Records**: their own table, nested in JSON, rather than a JSON column on the element. This keeps status filtering cheap and enforces one pending suggestion per element.
- **Evidence**: the run's harvest only, in this slice. During the design conversation the order was harvest, then aria, then inspect. The cross check showed two problems:
  - Aria candidates carry only role and name, so they can never reach the auto apply bar and have no fingerprint.
  - The scan inspector never logs in, so it would see the login page.

  Both moved to follow-ups. Harvest candidates have full attributes and fingerprints.
- **`locators_version`, not `updated_at`, for the edit race check**: run markers update `updated_at` on every run, so checking it would turn every heal into a suggestion.
- **Same fingerprint scores 1.0**: a candidate with the element's own fingerprint is the same element with fresh locators (for example stale css), which is the safest heal of all.
- **Rerun failure**: revert to a suggestion rather than keep the heal applied, so a wrong match can't stay in place quietly.
- **Toggle**: global config plus a per project switch, because project behavior is project data in this codebase.
- **Edit race**: a version check with a 409, rather than last write wins.
- **Shared failures**: heal once per key across test cases rather than per test case, so one element never gets conflicting heals.

Settled while writing:
- **Score weights** (test id 0.35, role 0.15, id or name 0.15, text 0.25, placeholder 0.10, type mismatch minus 0.30). Test id is the most stable signal Playwright guidance relies on, and text changes are the most common breakage. The runner up was equal weights, which ranks a placeholder match the same as a test id match.
- **LLM confidence cap** (`score + 0.2`), so the LLM can lift a decent candidate over the bar but not rescue a weak one. The runner up was trusting the LLM's confidence as is.
- **The fingerprint moves with the heal**, so the post run harvest matches the healed element instead of creating a duplicate. The runner up was skipping the harvest ingest after a heal, which loses new elements on that page.
- **Markers apply per result before the heal**, so failure counts land on the locators that actually failed. The runner up was applying all markers after the graph, which charges failures to the new locator.

## Amendment (2026-11-26): script repair from the RCA

**Context**: the log analyser (`ExecutionAnalysisAgent` / `LogAnalysisService`) already produces a structured RCA for every failed run: `failure_category`, `root_cause_summary`, `failing_step`, `trace_analysis`, `evidence`, and `confidence`. A locator heal only fixes the case where every locator for an element fails. Many automation failures sit outside a locator: a new confirm step, a changed step order, a stale assertion text, or a missing wait. After a locator heal, the rerun often gets further and then fails on one of these. The codebase already regenerates scripts from repair feedback (`PlaywrightAgent` with `repair_feedback`, used by `GroundedPlaywrightService` and `WorkflowService`), so the new work is mostly about deciding when to repair and how to undo it safely.

Decisions, each against a runner up:
- **Two stages rather than one combined pass**: the script is repaired from the RCA of the failure *after* the locator heal, with the healed keys in the prompt. A combined pass would repair from the pre-heal failure, which is usually just "element not found" and says nothing about the next step. The cost is a possible second rerun.
- **Auto-fix, verify, and restore rather than rerun only or suggest only**: this matches the locator heal model, where a checked claim is kept and a failed one goes back to a tester. Suggest only would leave every run failing until someone acts. Rerun only would mean the fix never sticks.
- **RCA category gate (automation issue only, with confidence of at least 0.6)**: rewriting a script on a `Product Bug` would hide the defect the test exists to catch. A `Test Environment Issue` isn't the script's fault. The runner up was repairing on any failure, which was rejected because it turns the tool into a way to make tests pass.
- **Assertion and expected result gates**: the main risk is an LLM "fixing" a test by dropping or loosening its assertions. The runner up, an LLM self check, can't be repeated and is weaker than a count.
- **Rerun only the failed test case, not the suite**: the EXECUTE path runs one test case. Rerunning passed cases would cost time and prove nothing about the fix. A whole suite option is a follow-up for the batch path.
- **AC-10 change**: a heal whose key resolved in the rerun, but whose test failed later, is no longer reverted at once. It waits for stage 2, and stage 2's outcome decides both the heal and the fix. Nothing stays applied unless the test passed with it, so the original safety rule holds.
- **No script version column**: the race check compares the stored script text with `old_script` or `new_script`. Script edits are rare and whole script, so a text compare is exact and needs no new column on test cases.
- **Data driven templates are out of this slice**: they are generated through a separate template and dataset path, and a repair would need to keep every dataset binding.

**Recorded from the build (2026-11-26)**: two choices the build made differ from the first amendment text. Both are kept.
- **Script heals in their own store, not nested in the test case record**: test cases are saved whole through the project repository (`save_test_cases`, `update_test_case`), from many places (script generation, validation, the test case PUT, the script PUT). A `script_heals` list nested in the record would be dropped or overwritten by any save that started from a copy read before the heal was written, and the apply step itself writes `playwright_script` back through that same path. A separate store with its own `mutate` keeps the single suggestion rule and retention in one read and write, with no coupling to test case saves. It also matches the locator heal design, where heals have their own table. The runner up, nesting the list and merging it on every test case save, would put heal logic into every test case write path. The cost: script and heal writes are two separate transactions, and a soft deleted test case leaves its heals behind on both backends (see Consequences and Follow-up in `index.md`).
- **Routes under `/testcases`, not `/test-cases`**: every existing test case route (list, update, delete, script, generate script, execute) uses `/api/v1/projects/{project_id}/testcases`, and the frontend API client builds paths from that prefix. A second hyphenated prefix for one feature would split the resource across two spellings. The spec text `test-cases` was a drafting slip, not a decision. The only constraint it adds is route order: the `script-heals` routes must be registered before `/testcases/{test_case_id}`.

## Migration notes

The migration is additive and needs no data change:
- the new table `object_locator_heals`
- nullable `executions.heal_rerun_of` and `executions.heal_ids`
- `object_elements.locators_version`, not null with server default 0
- `projects.self_healing_enabled`, not null with server default `true`

The script repair amendment adds one more additive migration: the table `test_case_script_heals` (revision `f4c2d8e6a915`).

Rollback is the downgrade, which drops them. The JSON store needs no migration, because missing keys read as an empty `heals` list, `locators_version` 0, and `self_healing_enabled` true. Script heals use their own file, `backend/database/script_heal_store.json`, and a missing file reads as no heals.
