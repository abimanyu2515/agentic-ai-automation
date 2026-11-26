# 0011. Verify: self healing locators

These are the steps I recommend for `/check verify`. Run them on the JSON store first, then repeat the starred ones with PostgreSQL.

## Automated

1. Run `pytest -q backend/tests/test_self_healing.py`. Every scenario in the spec's Critical test scenarios should pass, including the script repair ones. (AC-1 to AC-21)
2. Run `pytest -q` and check that there are no regressions in `test_object_repository.py`, `test_graph.py`, `test_log_analysis.py`, `test_run_history` (if present), and the bug report tests.
3. Start the backend, then run `cd backend && npx playwright test --project=chromium object-repository.spec.ts`. The accept suggestion flow should pass. (AC-14)

## Manual, against a safe test environment project

1. Start the backend with `uvicorn backend.main:app --reload --port 8000`. Pick a project with self healing on and a test case that uses `orx(page, '<page>.<element>')` for a button.
2. Run the test case once so the element shows `healthy` and its page is harvested.
3. In the target app, break every stored locator for that button while keeping it recognisable, then run again. `orx` tries the whole chain, so if any one locator still matches (for example an unchanged test id), the run passes and no heal happens. The scorer lowercases values, but `getByTestId` and `#id` are case sensitive. So change the case of the test id and id, and reword the label slightly. Keep only one similar button on the page.
   - Example: `<button data-testid="login-btn" id="login">Login</button>` becomes `<button data-testid="LOGIN-BTN" id="Login">Log in</button>`. That scores about 0.88 (test id 0.35, role 0.15, id 0.15, label 0.25 × 0.91), which clears `auto_apply_threshold: 0.85` deterministically.
   - If you can't edit the target app, serve a one-button `index.html` with `python -m http.server 5500` and use `http://127.0.0.1:5500` as the project's target URL.
   - Expect: the live log shows `self_healing_agent` applying a heal and rerunning once. The final result is passed. The report shows `Passed after heal` and a Self healing row. The element shows a `Healed` badge, its first locator is the new one, and the old one is a backup. (AC-1, AC-7, AC-9, AC-11, AC-14)
   - Expect: the first run's trace and screenshot open from `artifacts/<test_case_id>/heal-1/`, and the test case's flaky score did not go up. (AC-3, AC-11)
4. Change the button so it is ambiguous (two similar buttons, no test id) and run.
   - Expect: a `Suggestion` badge, no rerun, and no duplicate element on that page. Accept it in the drawer, run again, and the run passes. (AC-5, AC-8, AC-12)
5. Edit the element in the drawer while a suggestion is pending, then accept the suggestion.
   - Expect: 409 `element changed, review again`. (AC-12)
6. Make the healed locator wrong on purpose (point the new label at a different button that fails a later assertion).
   - Expect: the heal is reverted and shown as a suggestion, with the old locators back. (AC-10)
   - Since the 2026-11-26 amendment: if the RCA calls the later failure an automation issue, stage 2 runs first. When its rerun also fails, both the script fix and the heal become suggestions. To check stage 1 alone, set `script_repair.enabled: false`.
7. Turn the project's `Self healing` switch off and break a locator.
   - Expect: no heal, and the run fails as before. (AC-1)
8. Log in as a user with only `ObjectRepository: view`.
   - Expect: no Accept, Reject, or Revert buttons, and a direct POST returns 403. (AC-13)
9. (*) Set the repository provider to PostgreSQL and repeat steps 3 to 5. (AC-17)

## Value sourcing (added by /develop, 2026-11-25)

_One step per row of the spec's Value sourcing table. Each one varies the input that would break if the value came from the wrong place._

- [ ] Set `object_repository.healing.enabled: false` with the project switch on, then the other way round, and break a locator. Expect no heal and a straight route to bug reporting both times. (AC-1, healing enabled)
- [ ] Put the only harvested candidate on a page whose path does not match the element's `url_pattern`. Expect no heal and the log line `no harvest evidence for <key>`. (AC-2, candidates)
- [ ] Set `auto_apply_threshold: 0.95` and rerun the renamed element case. Expect a suggestion and no rerun. (AC-5, thresholds and margin)
- [ ] Edit the element as a tester between the failed run and the heal apply (see `test_a_tester_edit_between_deciding_and_applying_makes_a_suggestion`). Expect a suggestion, because the version is read after the markers are applied and checked inside `mutate`. (AC-7, version to check)
- [ ] After a heal rerun, check the `executions` rows. The failed attempt has no `heal_rerun_of`, the rerun row's `heal_rerun_of` equals the failed row's id, and its `heal_ids` lists the heal. (AC-9, `heal_rerun_of`)

Note for step 3 of the manual list above: a locator that still matches in the browser stops `orx` from failing, so a heal needs every stored locator to fail. With the AC-4 weights, a candidate that matches the test id, role, and id (ignoring case) with a close label clears the deterministic bar. If the label is very different, the score drops and the heal goes through the LLM path, where confidence is capped at score + 0.2.

## Heal API (added by /develop, 2026-11-25, milestone 3)

_Routes under `/api/v1/projects/{project_id}/object-repository`. Make a suggestion first (an unclear match, or a failed heal rerun)._

- [ ] As a Stakeholder, `GET /heals` returns 200 with the heal (`key`, `status`, `confidence`). `POST /heals/{id}/accept`, `/reject`, and `/revert` each return 403. A Tester who is not on the project also gets 403. (AC-12, AC-13)
- [ ] `GET /heals?status=applied` returns only applied heals, newest first. `?status=bogus` returns 422. (AC-12)
- [ ] As a Tester, accept the suggestion. Expect 200 with `heal.status = accepted`, `decided_by` set to you, and the element's locators led by the candidate's. Its `latest_heal.id` is the heal, its health is `needs_review`, and `manually_edited` is unchanged. (AC-12)
- [ ] Accept the same heal again. Expect `409 heal is accepted`. Revert a suggestion. Expect `409 heal is suggested`. (AC-12)
- [ ] Make a new suggestion, edit the element in the drawer, then accept. Expect `409 element changed, review again`, and the heal is still `suggested`. (AC-12, version to check)
- [ ] Revert the accepted heal. The old locators and fingerprint come back, `locators_version` goes up by 1, and the heal is `reverted`. (AC-12)
- [ ] POST a heal id under another project's path. Expect 404. Delete the element, then accept its suggestion. Expect 404, and the heal is no longer in `GET /heals`. (AC-12, security model)
- [ ] Check the audit log. Each accept, reject, and revert adds one `OBJECT_REPOSITORY` row whose changes say `entity: object_locator_heal`, `heal_accepted`, `heal_rejected`, or `heal_reverted`, with the key and the old and new main locator. Automatic applies have no user. (AC-13)
- [ ] (*) Repeat the accept, reject, and revert steps on PostgreSQL. Expect the same results. (AC-17)
- [ ] `python -m pytest -q backend/tests/test_self_healing.py` reports 25 passed, including `test_accept_reject_revert_on_the_sql_store`. (AC-12, AC-16, AC-17)

## Object Repository UI and the Self healing switch (added by /develop, 2026-11-25, milestone 4)

_Make one suggestion and one applied heal first (an unclear match, and a confident one). Hard refresh so `app.js?v=36` loads._

- [ ] Open the Object Repository view. The attention card says `N self healing suggestion(s) waiting`, counting every pending suggestion in the project. With nothing broken or needing review, clicking it opens the suggestion's page with its element expanded. (AC-14, value: `GET /heals?status=suggested`)
- [ ] The element with a suggestion shows `Suggestion` next to its health badge, and the applied one shows `Healed`, both as words, not only colour. An element whose latest heal is `rejected` or `reverted` shows no heal badge. (AC-14, value: `latest_heal.status`)
- [ ] As a Tester, open the suggestion's drawer with the keyboard (Tab to Edit, Enter). The Healing section shows `Waiting for review`, the confidence as a percent, the method, the reason, and the old and new locators as Playwright calls. Tab reaches Accept and Reject. (AC-14)
- [ ] Press Accept. The button reads `Accepting...` while it waits, then a toast confirms. The drawer reloads with status `Accepted`, a Revert button, and the new main locator in the form. The row badge becomes `Healed` and the card count drops by one. (AC-12, AC-14)
- [ ] On another suggestion, press Reject. The status becomes `Rejected`, no buttons remain, and the row loses its heal badge. (AC-14)
- [ ] On an applied heal, press Revert and confirm. The old locators come back in the form and the status becomes `Reverted`. Cancel the confirm once first and check nothing changes. (AC-12, AC-14)
- [ ] Make a suggestion, then in a second tab edit that element, then press Accept in the first tab. The section shows `Could not accept this heal: element changed, review again` and the buttons work again. (AC-12, AC-14 error state)
- [ ] An element with no heal shows the Healing section's empty note in its drawer. (AC-14 empty state)
- [ ] As a Stakeholder, the drawer can't be opened. Expanding a row with a heal shows the Healing section with no buttons. (AC-13, AC-14)
- [ ] In project settings, the `Self healing` switch is on for a new project. Turn it off, save, and reload: it stays off, and `GET /api/v1/projects/{id}` returns `self_healing_enabled: false`. Run a test case with a broken locator: no heal is made and the run goes straight to bug reporting. Turn it back on. (AC-1, AC-13, AC-14, value: `self_healing_enabled`)
- [ ] With the backend running, `cd backend && OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/object-repository.spec.ts --project=chromium` reports 8 passed, including the two `Object Repository self healing (spec 0011)` tests. (AC-14)

## Script repair from the RCA (amendment 2026-11-26)

_Use a safe test environment project with self healing on and `object_repository.healing.script_repair.enabled: true`. Use a test case that is not data driven._

Manual:
1. Run the test case once so it passes. Expect no `self_healing_agent` phase in the live log, and no heal or script heal saved. (AC-1, no errors, no processing)
2. Keep every locator valid but change the flow, for example by adding a confirm dialog before the success message. Run again.
   - Expect: the RCA says `Flaky Test / Automation Issue`, the log shows `self_healing_agent` repairing the script and rerunning once, and the final result is passed.
   - Expect: the report shows `Passed after heal` with a script row, and the test case shows a `Script repaired` badge with a diff of the old and new script.
   - (AC-18, AC-19, AC-21, AC-11, AC-14)
3. Combine both changes: rename the button as in manual step 3 above, and add the flow change. Expect a stage 1 heal, then a rerun that resolves the key but fails later, then a stage 2 script fix, then a second rerun that passes. Both badges show, and there were no more than 2 reruns. (AC-10, AC-18)
4. Break the app with a real product defect, for example by making the success message show the wrong total. Expect a `Product Bug` RCA, no script change, and a bug report draft. (AC-18)
5. Make the repair impossible, for example by removing the target page's success state. Expect stage 2 to run, the rerun to fail, and the old script to be back. Both the script heal and any stage 1 heal show as suggestions. (AC-10, AC-21)
6. Edit the test case's script while a script suggestion is pending, then accept it. Expect 409 `script changed, review again`. (AC-21, AC-12)
7. Set `script_repair.enabled: false` and repeat step 2. Expect no script change, and the failure goes to bug reporting. (AC-18)
8. As a user with only test case view permission, expect no Accept, Reject, or Revert buttons on the script heal, and a direct POST returns 403. (AC-13)
9. (*) Repeat steps 2, 5, and 6 on PostgreSQL. (AC-17)

Value sourcing:
- [ ] Give the analysis StubLLM a `Product Bug` with confidence 0.9, then `Flaky Test / Automation Issue` with confidence 0.5, then 0.7. Expect script repair only for the last one. (AC-18, RCA category and confidence)
- [ ] Leave one `ok: false` marker for a key that wasn't healed. Expect no script repair, even with an automation issue RCA. (AC-18, locators resolved)
- [ ] Capture the script generation prompt. It contains the RCA's `root_cause_summary` and `failing_step` inside the untrusted data delimiters, lists the healed key's new locator, and contains no credential or test data value. (AC-19, feedback, keys in the prompt)
- [ ] Make the StubLLM return a script with one fewer `expect(`. Expect a suggestion whose reason names the assertion gate, and no rerun. (AC-20, gates)
- [ ] After a stage 2 rerun fails, the stored `playwright_script` equals the heal's `old_script` byte for byte. (AC-21, script to restore)
- [ ] After two stages, the `executions` rows for the test case form a chain: run 1, then rerun 1 with `heal_rerun_of` = run 1, then rerun 2 with `heal_rerun_of` = rerun 1. `run_history` counts only rerun 2. (AC-11, AC-21)

## Script repair build (added by /develop, 2026-11-26)

_Steps for what the amendment build actually shipped. Script heals live in their own store (`backend/database/script_heal_store.json`, or the `test_case_script_heals` table), and the routes use the existing `/testcases` path. /architect recorded both in the spec on 2026-11-26._

### Commands
- [ ] `python -m pytest -q backend/tests/test_script_repair.py backend/tests/test_self_healing.py` reports 46 passed. → AC-1, AC-10, AC-11, AC-15, AC-17 to AC-21
- [ ] `python -m alembic current` shows `f4c2d8e6a915 (head)`, and the `test_case_script_heals` table has the partial unique index `uq_test_case_script_heals_one_suggested`. → AC-17, AC-21
- [ ] With the backend running, `cd backend && OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/script-healing.spec.ts --project=chromium` reports 1 passed. → AC-14

### UI / manual
- [ ] Run a test case whose locators are fine but whose flow changed (a new Confirm dialog). The live log shows `Self Healing: Repairing the script`, then one rerun. On the Test Cases view the row shows `Script repaired` next to the validation badge, and the expanded row's `Script healing` section shows the RCA, the root cause, and the old and new script with added and removed lines marked in text. → AC-14, AC-18, AC-21
- [ ] The same run's report has one `self_healing` row with `kind: script`, the test case title, the RCA category, and `rerun_outcome: passed`, and no script text. → AC-11
- [ ] Rename the button and change the flow together. The `executions` rows form a chain of three: rerun 1's `heal_rerun_of` is run 1, and rerun 2's is rerun 1. Run history counts only rerun 2. → AC-10, AC-11
- [ ] Edit the script in the workspace after a run, then run and fail again. Make the repair fail its rerun. The edited script is left alone, a warning is logged, and the heal shows `Waiting for review`. → AC-21, script to restore
- [ ] On a `Script suggestion`, press Accept with the keyboard. The button reads `Accepting...`, a toast confirms, and the row turns `Script repaired` with only Revert left. Revert asks for confirmation first. → AC-12, AC-14
- [ ] Edit the script, then press Accept on the older suggestion. The section shows `Could not accept this script fix: script changed, review again`, and the buttons work again. → AC-12, AC-14 error state
- [ ] As a Stakeholder, `GET /api/v1/projects/{id}/testcases/script-heals` returns 403, and the Script healing section shows no buttons. → AC-13

### Value sourcing
- [ ] Put a vault credential value in the failure's root cause (for example, a failed login message that echoes the user). The repair prompt shows it as `[redacted]`. → AC-19, feedback
- [ ] Make the only harvested page different from the page the script uses. The repair still runs, and `dom_context.pages` lists only the harvested page. → AC-19, page evidence
- [ ] After a stage 1 heal, capture the repair prompt. The OBJECT REPOSITORY section lists the healed locator first. → AC-19, keys in the prompt
- [ ] Turn off `script_repair.enabled` and the project switch in turn. In both cases there's no script repair, and the failure goes on to bug reporting or stops there. → AC-18, trigger
- [ ] Run the local validator against a real harvest. If the old script itself fails `validate_playwright_script` on harvest evidence (role locators on pages that weren't harvested), every repair becomes a suggestion. Note how often this happens, so /architect can decide whether the gate should only count new issues. → AC-20, gates

### Acceptance criteria coverage
- AC-1: test_a_passed_run_is_never_processed · AC-10: the two stage tests and test_a_heal_whose_key_fails_again_is_reverted_without_a_script_repair · AC-11: the report rows and the execution chain · AC-12, AC-13: test_script_heal_routes_permissions_and_latest_script_heal · AC-14: e2e/script-healing.spec.ts · AC-15: test_a_script_repair_error_never_breaks_the_run · AC-17: test_sql_store_keeps_one_suggestion_and_round_trips · AC-18: test_only_a_confident_automation_issue_changes_a_script, test_trigger_rules_for_one_result · AC-19: feedback and dom_context assertions in the stage 2 test · AC-20: the gate tests · AC-21: apply, restore, race, and decision tests

## Script heal store hardening (added by /develop, 2026-11-26)

_Steps for the three follow-ups built after the store and route update: the heal row is written before the script, a corrupt JSON store raises, and the store file is ignored by git._

### Commands
- [ ] `python -m pytest -q backend/tests/test_script_repair.py backend/tests/test_self_healing.py` reports 48 passed. → AC-15, AC-17, AC-21
- [ ] `git check-ignore -v backend/database/script_heal_store.json` prints the `.gitignore` line. → project rule (local databases stay out of source)

### Manual
- [ ] Make the test case save fail during a stage 2 apply (for example, stop PostgreSQL right after the repair is generated). The test case keeps its old script, the heal shows as `Script suggestion` with a reason saying the script could not be saved, no rerun starts, and the failure goes on to bug reporting. → AC-21 (apply order), AC-15
- [ ] With the JSON provider, break `backend/database/script_heal_store.json` (cut off its last characters), then open the Test Cases view and list script heals. The list route returns an error, the test case rows still load (with no script badges), and the file is unchanged afterwards. → AC-17, Value sourcing `latest_script_heal`
