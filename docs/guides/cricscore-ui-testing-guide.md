# CricScore — End-to-End UI Testing Guide

Step-by-step guide to test the **CricScore** project (`https://cricscore-wheat.vercel.app/`)
through this platform's UI, from login to automated browser execution.

> You already created a project named **CricScore** with target URL
> `https://cricscore-wheat.vercel.app/`. This guide starts right after that step.
> Follow it in order: **Requirements → Scenarios → Test Cases → Test Automation → Executions/Reports**.

---

## 0. Prerequisites and startup

### 0.1 Start the backend + frontend

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

The frontend is a vanilla SPA served by FastAPI. There is no separate frontend dev server.

### 0.2 Start the Test Automation workspace (required before Section 6)

This is the separate Next.js app under `backend/playwrightt/` that actually runs Playwright in a browser.

```bash
pnpm --dir backend/playwrightt install
pnpm --dir backend/playwrightt dev
```

Default URL:

```text
http://localhost:3000
```

The main UI opens this workspace automatically with query parameters
(`?project_id=...`, `?project_id=...&test_case_id=...`,
`?project_id=...&test_case_ids=...&test_cycle_id=...`).
If you changed its port, update **Master Settings → General Settings** (`playwright.workspace_url`)
so the **Open Test Automation Workspace** button points to the right place.

### 0.3 Log in

1. Open `http://localhost:8000`.
2. You see the **Sign In** overlay (`#login-overlay`).
3. Enter:
   - Email Address: `admin@agenticai.com`
   - Password: `admin123`
4. Click **Sign In**.

On success the sidebar (`#sidebar-container`) and top nav (`#project-select-wrapper`) appear.

### 0.4 Select the CricScore project

1. In the top nav, click the project combobox (`#project-combobox-display`, label `#project-combobox-label`).
2. Search `CricScore` in `#project-combobox-search`.
3. Click **CricScore** in `#project-combobox-list`.

What happens:

- `localStorage.active_project_id` is set.
- Sidebar Level-1 items appear:
  `Requirement`, `Scenarios`, `Test Cases`, `Test Automation`, `Execution Workspace`, `Reports`.
- The app loads Releases/Cycles, Requirements, Scenarios, Test Cases, Documents, Executions.
- You land on **QA Testing Dashboard**.

### 0.5 Verify the target URL (important)

The runner has **no global/demo URL**. Every script uses the selected project's `target_url`.

1. Go to **Master Settings** (sidebar, `data-view="settings"`).
2. Open the **Project Settings** tab.
3. Check:
   - Target Application Base URL (`#project-settings-url`) =
     `https://cricscore-wheat.vercel.app/`
   - Target Application Username / Password: only fill these if CricScore has a login.
     CricScore is a public score-viewing app, so normally leave them empty.
4. Click **Save Project Settings**.

If the URL is wrong, fix it here. Generated Playwright scripts will navigate to this URL.

Optional but recommended:

- **Master Settings → General Settings**: confirm LLM provider/model/key, browser = `chromium`, headless setting, default scenario count.
- Note the **Workflow Progress** timeline on the dashboard (`#workflow-timeline-vertical`):
  `Requirement Analysis → Scenario Drafting → Human Scenario Approval → TestCase Generation → Playwright Scripting → Automated Execution → Execution Reporting`.
  Use it to see where CricScore currently stands.

---

## 1. Creating and importing requirements for CricScore

Open **Requirement** in the sidebar (`data-view="requirements"` → `#requirements-view`).

The page has:

- JIRA input (`#jira-story-key-input`) + **Import JIRA Story** (`#btn-import-jira-story`)
- **Import Requirement File** button (opens hidden `#import-req-file`, accepts `.xlsx,.xls,.pdf,.docx,.doc,.txt`)
- Dropzone (`#req-upload-dropzone`): `Drag and drop requirement document files here to parse (XLSX, PDF, DOCX, TXT)`
- Table (`#requirements-list-container`)

Requirements are scoped by Release if one is selected in `#release-select`. For your first CricScore pass, you can leave Release/Cycle unselected; everything still works at project scope.

### 1.1 Option A — Create requirements manually (recommended first step)

Use this to learn the flow with 4–6 focused CricScore requirements.

1. Click **+ Create Requirement** in the top nav (`#btn-create-requirement`).
2. In **Add Requirement** modal (`#create-requirement-modal`), fill:
   - Requirement Title (`#req-title`)
   - Scope Details (`#req-desc`)
   - Priority (`#req-priority`: `low` / `medium` / `high`)
   - Business Domain (`#req-domain`, default `general`)
3. Click **Save Requirement**.
4. The app refreshes and navigates to **Requirement**.

Copy/paste examples for CricScore:

| #   | Title                            | Description (paste into Scope Details)                                                                                                                                                                                                                         | Priority | Business Domain |
| --- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | --------------- |
| 1   | CricScore homepage live scores   | Homepage at `https://cricscore-wheat.vercel.app/` must display the list of live/recent cricket matches. Each match card shows team names, scores, overs, status (Live/Result/Upcoming). Page loads with no console errors and cards are visible without login. | high     | Scores          |
| 2   | Match detail scorecard view      | Clicking a match card navigates to a match detail/scorecard view showing innings breakdown, batsmen scores, bowlers figures, and match status. Browser Back returns to the homepage list.                                                                      | high     | Scorecard       |
| 3   | Upcoming fixtures section        | Homepage or fixtures section lists upcoming matches with teams, venue, date/time. Empty state shows a friendly message if no fixtures exist.                                                                                                                   | medium   | Fixtures        |
| 4   | Points table / standings         | Points table page/section shows team standings sorted by points then NRR. Table is readable on desktop and mobile widths.                                                                                                                                      | medium   | Standings       |
| 5   | Search and filter matches        | User can search or filter matches by team name, status (Live/Upcoming/Result), or tournament. No-results state is handled. Filter reset restores the full list.                                                                                                | medium   | Search          |
| 6   | Responsive navigation and header | Header/nav (logo, links) works on desktop and mobile. Mobile menu opens/closes. All links navigate without 404s. Page renders without horizontal scroll at 390px width.                                                                                        | low      | Navigation      |

Create at least requirements 1–2 before continuing. You need ≥1 requirement to generate scenarios.

### 1.2 Option B — Import requirements from a file

Accepted: `.xlsx`, `.xls`, `.pdf`, `.docx`, `.doc`, `.txt` (plus `.json`/`.yaml` Postman/Swagger in the API path).

#### Excel format (most predictable)

Create an `.xlsx` with these headers (case/spacing-insensitive):

```text
RequirementId | Requirement | Priority | Module
```

Example rows:

```text
REQ-001 | Homepage shows live cricket matches with team names, scores, overs and status | high | Scores
REQ-002 | Match detail page shows innings, batsmen, bowlers and match status | high | Scorecard
REQ-003 | Upcoming fixtures list shows teams, venue and date/time | medium | Fixtures
REQ-004 | Points table sorted by points then NRR | medium | Standings
REQ-005 | Search/filter matches by team, status or tournament | medium | Search
```

Rules used by the parser (`backend/services/project_service.py:parse_requirements_from_file`):

- `Requirement` / `Description` / `Title` column is the requirement text. First column is used if none matches.
- `RequirementId` / `Id` defaults to `REQ-001`, `REQ-002`, …
- `Priority` must be `low`/`medium`/`high`, otherwise `medium`.
- `Module` / `BusinessDomain` / `Domain` defaults to `general`.

To import:

1. In **Requirement** view, either:
   - Click **Import Requirement File** and pick the `.xlsx`, or
   - Drag/drop it onto `#req-upload-dropzone`.
2. You see `Importing and parsing '<filename>' requirements...`.
3. Each row is created, then enriched by the LangGraph **INGEST** workflow
   (`RequirementAnalystAgent → FeatureInventoryAgent`).
4. The Requirements table refreshes with the new rows.

#### PDF / DOCX / TXT format

The text parser looks for explicit IDs first:

```text
REQ-001: Homepage shows live cricket matches with team names, scores, overs and status.

REQ-002: Match detail page shows innings, batsmen, bowlers and match status.

QA-101: Points table sorted by points then NRR.
```

Accepted ID patterns: `REQ-123`, `Requirement 123`, `QA-101` / any `LETTERS-123`, followed by `:`.
If no IDs are found, the file is split on blank lines (`\n\n`) into paragraph blocks;
blocks shorter than 25 chars are skipped and auto-numbered `REQ-001`, ….
Priority/domain are guessed from keywords (`critical/urgent → high`, `login/auth/search/...` → domain).

Use the same drag/drop or browse flow as Excel.

### 1.3 Option C — Import a JIRA story (optional)

Only if JIRA is configured in **Master Settings → General Settings → JIRA Integration Settings**.

1. In **Requirement** view, type the key in `#jira-story-key-input`, e.g. `QA-101`.
2. Click **Import JIRA Story**.
3. You see `Fetching JIRA Story 'QA-101' and running Analyst pipeline...`.
4. The story summary/description becomes a requirement linked by `jira_issue_key`.

### 1.4 Verify requirements

- Table (`#requirements-list-container`) shows one row per requirement.
- Each row has **Select/Go to Scenarios** action (`onSelect`): clicking it sets that requirement
  as active and navigates to **Scenarios**.
- Dashboard **Workflow Progress** step 1 (`Requirement Analysis`) should flip to `COMPLETED`.
- If import failed, you get `Import failed: ...`; check file type/size and retry with the Excel template above.

---

## 2. How scenarios are created / generated

Open **Scenarios** (`data-view="scenarios"` → `#scenarios-view`).

Layout:

- Search (`#scenario-req-search`) + **Select Active Requirement** (`#scenario-req-select`)
- Toolbar (`#scenarios-actions-toolbar`): **Bulk Approve**, **Bulk Reject**,
  **Count** (`#sc-generation-count`, default `3`, min 1 max 50),
  **Generate Scenarios** (`#btn-generate-scenarios`)
- List (`#scenarios-list-container`)

Scenarios are **per-requirement**. You must pick an active requirement first.

### 2.1 Generate scenarios for one CricScore requirement

1. Select a requirement, e.g. `REQ-001: Homepage ...`, in `#scenario-req-select`
   (or use `#scenario-req-search` to filter by title/ID/description).
2. Set **Count**, e.g. `3`–`5` for the first pass.
3. Click **Generate Scenarios**.
4. If scenarios already exist for that requirement, the **Existing Scenarios Found** modal
   (`#confirm-scenarios-modal`) appears:
   - **Append** (`#btn-confirm-sc-append`): keep old + add `Count` new.
   - **Replace Existing** (`#btn-confirm-sc-replace`): delete old, generate fresh.
   - First run: no modal, it generates directly in `replace` mode.
5. You see `AI is generating N scenarios for this requirement...`.
   Backend operation: `POST /api/v1/projects/{project_id}/requirements/{requirement_id}/generate-scenarios`
   with `{count, mode}`, running `ScenarioAgent → HumanApprovalAgent (Gate 1)`.
6. Table refreshes with scenario name, description, priority, confidence, approval state.

Repeat for each CricScore requirement. Good starting counts:

- Homepage (REQ-001): `5`
- Scorecard (REQ-002): `4`
- Fixtures/Standings/Search/Nav: `3` each

### 2.2 Review scenarios (Human Gate 1 — do not skip)

Each scenario row supports:

- **Approve** → `PUT .../scenarios/{id}` `{approved: true}`
- **Reject** → `{approved: false, rejected: true}`
- **Edit/Save** (name, description, priority)
- **Duplicate** (clone a good scenario and tweak it)
- **Delete** (with confirm)
- **Add Note** (review comment)
- **Sync JIRA** (push requirement's scenarios to JIRA)

Bulk flow:

1. Tick checkboxes (`.scenario-checkbox`).
2. Click **Bulk Approve** or **Bulk Reject**.

Rule for CricScore:

- Approve at least **1 scenario per requirement** you want test cases for.
- **Test Cases generation is blocked until ≥1 scenario is approved.**
  The Test Cases button stays disabled with
  `Generate Test Cases (Approve at least one Scenario)` otherwise.
- Reject vague/duplicated AI scenarios rather than leaving them pending.
  Pending scenarios show in the dashboard **Scenario Approval** donut as pending.

### 2.3 Expected result

- `#scenarios-list-container` shows approved scenarios with badges.
- Dashboard **Scenario Approval** donut updates (approved/rejected/pending).
- **Workflow Progress** step 2 (`Scenario Drafting`) → `COMPLETED`,
  step 3 (`Human Scenario Approval`) → `COMPLETED` once you approve something.

---

## 3. How the Test Cases section works

Open **Test Cases** (`data-view="testcases"` → `#testcases-view`).

Layout:

- Search (`#tc-req-search`) + **Select Active Requirement** (`#tc-req-select`)
- Toolbar (`#testcases-actions-toolbar`): **Bulk Approve**, **Bulk Reject**,
  **Generate & Evaluate Test Cases** (`#btn-generate-testcases`)
- List (`#testcases-list-container`)
- Footer (`#testcase-framework-footer`): **Framework** (`#project-framework-select`:
  `playwright` / `selenium` / `cucumber`) + bulk script buttons
  (`#testcase-bulk-script-container` → `#btn-generate-all-scripts`, `#btn-run-selected-batch`)

Important: the Test Cases list shows **only test cases whose parent scenario is approved**.
Unapproved scenarios' cases are hidden by design.

### Jira traceability

Open **Jira Traceability & Status** from the project sidebar
(`data-view="jira-traceability"` → `#jira-traceability-view`) to view the
selected requirement's per-scenario Jira key and sync status
(`Done` / `In Progress` / `Sync Pending` / `Sync Failed` / `Pending`).
Choose a requirement with `#jira-trace-req-select`.

### 3.1 Generate test cases

1. Select the active requirement (e.g. REQ-001).
2. Confirm at least one scenario is approved in **Scenarios**.
3. Click **Generate & Evaluate Test Cases**.
4. You see a loading indicator while test cases are generated and evaluated.
   Backend: `POST .../requirements/{id}/generate-testcases` →
   `TestCaseAgent → EvaluationAgent → HumanApprovalAgent (Gate 2)`.
   `EvaluationAgent` does Jaccard duplicate detection and relevance scoring and sets
   `evaluation_status` / `evaluation_reason`.
5. Table refreshes. Each test case has title, preconditions, steps, expected result,
   priority, confidence, evaluation status, execution type.

The browser stops waiting after five minutes and restores the test-case view. A timeout
does not guarantee the backend operation was cancelled; check backend logs before retrying.

If the button is disabled, go back to **Scenarios** and approve at least one scenario.

### 3.2 Review test cases (Human Gate 2)

Per-row actions (`Components.TestCasesTable`):

- **Approve** → `{evaluation_status: 'approved'}`
- **Reject** → `{evaluation_status: 'rejected'}`
- **Edit/Save** (steps, expected result, priority, etc.)
- **Delete** (with confirm)
- **View Script** → opens the Playwright workspace for that single test case
  (`openPlaywrightScriptWorkspace`: `workspace_url/?project_id=...&test_case_id=...`)
- **Generate Script** → `POST .../testcases/{id}/generate-script` (per-row button shows `Generating...`)
- **Add Note** (review comment)

Bulk flow: tick `.tc-checkbox` rows → **Bulk Approve** / **Bulk Reject**.

For CricScore:

1. Approve the happy-path cases first (homepage loads, match card click, scorecard visible).
2. Keep 1–2 negative cases (no fixtures, search no-results, mobile width).
3. Reject exact duplicates flagged by the EvaluationAgent.

### 3.3 Generate Playwright scripts (still in Test Cases view)

Set **Framework** to `playwright` (CricScore is a web UI; keep this value).

Two paths:

**A. One script at a time:** click **Generate Script** on a row. Button flips to `Generating...`,
then the row gains a script link/badge.

**B. All approved at once:**

1. Approve ≥1 test case → `#testcase-bulk-script-container` appears.
2. Click **Generate Playwright Scripts for All Approved Test Cases**
   (`#btn-generate-all-scripts`, shows `Generating Scripts in Background...`).
3. It loops `generatePlaywrightScript(project_id, tc.id, framework)` for each approved case.
4. Refresh and confirm each approved row now has a script.

Classification note: `PlaywrightAgent` (+ Automation Orchestrator) marks each case
`UI Automated` / `API Automated` / `Manual Testing`. Only automatable cases get executable scripts;
manual ones keep human-readable steps. CricScore cases should almost all be `UI Automated`.

### 3.4 Expected result

- Approved test cases with scripts.
- Dashboard **Workflow Progress**: `TestCase Generation → COMPLETED`, `Playwright Scripting → COMPLETED`.
- You are now ready to execute.

---

## 4. How the Test Automation workspace works and how to automate tests

There are two connected surfaces. Do not confuse them:

| Surface                                                                                                       | Where                             | Purpose                                                                                         |
| ------------------------------------------------------------------------------------------------------------- | --------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Test Automation** (`data-view="playwright"` → `#playwright-view`)                                           | Main UI                           | Launcher page with **Open Test Automation Workspace** (`#btn-launch-playwright-workspace`)      |
| **Test Automation Workspace** (Next.js)                                                                       | `http://localhost:3000` (new tab) | Monaco editor, browser preview, timeline, console logs, video/trace. Actually runs the browser. |
| **Execution Workspace** (`data-view="executions"` → `#executions-view`, observer `#execution-workspace-view`) | Main UI                           | History + real-time observer (terminal, progress, triage, artifacts). Read-only monitoring.     |
| **Reports** (`data-view="reports"` → `#reports-view`)                                                         | Main UI                           | Aggregated pass/fail, duration, JUnit-style logs, US/TC/Status filters.                         |

### 4.1 Path 1 — Run Selected Batch (recommended for CricScore)

Use this to run multiple CricScore cases together.

1. In **Test Cases**, tick the approved, scripted cases you want (`.tc-checkbox`).
   Start with 2–3 homepage cases to validate the pipeline.
2. Click **Run Selected Batch** (`#btn-run-selected-batch`, green).
3. The app:
   - Checks that the Playwright workspace is running and opens a progress tab.
   - Finds selected cases with no `playwright_script` and generates them in groups,
     showing progress as scripts finish.
   - Then navigates the progress tab to the workspace:
     ```text
     {workspace_url}/?project_id={CricScore_id}&test_case_ids={id1,id2,...}&test_cycle_id={cycle_id_or_empty}
     ```
4. In the workspace tab, run the batch. Watch browser preview + logs there.
5. Back in the main UI, open **Execution Workspace** (sidebar) to monitor:
   - **Execution Stream Terminal** (live logs, survives refresh via `EventSource`
     `/api/v1/projects/.../batches/{BATCH-id}/stream`)
   - **Batch Execution Progress** bar + ETR
   - Passed/Failed/Skipped counters
   - Logical Application State (Current State / Next Required State / Navigation Decision / Retry / Dataset Row)
6. When `Completed`/`Error`, the stream closes with `Finished` and data refreshes.

If you use Releases/Cycles: pick Release in `#release-select` and Cycle in `#cycle-select`
before the batch run so executions are tagged with `test_cycle_id` and appear correctly
in dashboard scope filters (`Project Overview` / `Release Info` / `Test Cycle Info`).

### 4.2 Path 2 — Open the workspace directly

1. Go to **Test Automation** in the sidebar.
2. Read the **Test Automation Execution Subsystem** card.
3. Click **Open Test Automation Workspace**.
4. It opens `{workspace_url}/?project_id={CricScore_id}`.
5. Pick test cases inside the workspace, edit scripts in Monaco if needed, and run.

Use this path to inspect/edit a script before a batch run.

### 4.3 Path 3 — Single test case (debug one CricScore flow)

- In **Test Cases**, click **View Script** on one row → opens
  `?project_id=...&test_case_id=...`.
- Or use the API equivalent `executeTestCase(project_id, testCaseId)` for a single run.
- The observer streams from `.../testcases/{id}/execution-stream`.
- Prefer this when a homepage or scorecard test fails and you want a tight loop.

### 4.4 What execution does behind the scenes

`ExecutionAgent → ExecutionAnalysisAgent → DefectManagementAgent → ReportAgent`:

- Runs `npx playwright test` via `backend/services/playwright_runner.py`.
- Captures screenshot, video, trace per execution ID under `backend/playwrightt/artifacts/`.
- Streams logs to the observer terminal.
- On failure: categorizes (Product Bug / UI / Data / Environment / Flaky / Timeout),
  writes root-cause summary, flags retry/retest, optionally creates a JIRA bug
  (visible as `jira_bug_id` / `jira_bug_url`).
- Generates JUnit/JSON reports consumed by **Reports**.

The workspace queue is process-local, one active execution at a time
(`public/artifacts/active_executions.json`). Do not launch overlapping batches.

### 4.5 Verify execution

1. **Execution Workspace** (`#executions-view` → `#executions-list-container`):
   chronological log with status, timing, replay links.
2. Click an execution → **Execution Workspace Observer** (`#execution-workspace-view`):
   - Stage strip: Queued → Preparing Env → Running → Analysis → Jira Sync → Reporting → Completed
   - **AI Execution Triaging Findings** (failure category, root cause, retry/retest flags)
   - **Defect Lifecycle Link** (JIRA bug if created)
   - **Execution Stream Terminal** (logs)
   - **Execution Run Artifacts** (View Screenshot / Replay Video; empty state if none captured)
3. **Reports** (`#reports-view`):
   - Summary cards: Pass/Total, Failed, Errors/Blocked, avg duration, success %.
   - Batch vs individual run selector.
   - Filters: User Story (`#filter-us-number`, e.g. `US01`), Test Case (`#filter-tc-number`, e.g. `TC01`),
     Status (`#filter-status`: passed/failed/error/skipped) → **Apply Filters** / **Clear**.
     IDs come from `test_case_ref_id` formatted `USxx-TCxx`.
4. **QA Testing Dashboard**: pass ratio, coverage, execution trend, recent executions,
   defect status, and **Workflow Progress** should now show all 7 steps `COMPLETED`.

---

## 5. Suggested CricScore first pass (30–45 minutes)

1. Select **CricScore**, verify target URL is `https://cricscore-wheat.vercel.app/`.
2. Create manual requirements 1 + 2 from Section 1.1 (homepage + scorecard).
3. **Scenarios**: select REQ-001 → Count `5` → Generate → Approve 3 best, Reject rest.
   Same for REQ-002 with Count `4`.
4. **Test Cases**: select REQ-001 → Generate & Evaluate → Approve 3–4 (include 1 negative).
   Same for REQ-002.
5. Framework = `playwright` → **Generate Playwright Scripts for All Approved Test Cases**.
6. Tick 2 homepage cases → **Run Selected Batch** → watch workspace + observer.
7. Check **Executions History** + **Reports**. Fix/approve and re-run failures one at a time
   via **View Script**.
8. Expand to fixtures/standings/search/nav requirements once green.

---

## 6. Troubleshooting

| Symptom                                                          | Cause / Fix                                                                                                                                               |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Generate Scenarios` alerts `timed out` / `Scenario generation failed` | LLM endpoint slow or unreachable. Check the uvicorn terminal for `structured_generate attempt N failed`, verify LLM provider/model/key in **Master Settings → General Settings**, and retry. Known: `deepseek-ai/deepseek-v4.1-flash` on Nvidia NIM hangs without responding; `openai/gpt-oss-20b` (same provider/key) is verified working. Do not re-save settings while the provider dropdown is blank. |
| `Generate Test Cases` disabled                                   | No approved scenario for the active requirement. Go to **Scenarios** → approve ≥1.                                                                        |
| `Generate Playwright Scripts...` button hidden                   | No approved test case for the active requirement. Approve TCs first.                                                                                      |
| `Run Selected Batch` says `Please select at least one test case` | No `.tc-checkbox` ticked. Tick rows first.                                                                                                                |
| Workspace is reported unavailable / browser says connection refused | `backend/playwrightt dev` is not running, or `playwright.workspace_url` is incorrect. Start it with `pnpm --dir backend/playwrightt dev` or correct the URL in settings. |
| Script navigates to wrong site                                   | Project Settings target URL is wrong. Set to `https://cricscore-wheat.vercel.app/` and regenerate scripts.                                                |
| No executions in Reports                                         | Nothing executed yet, or Release/Cycle scope filter hides them. Clear filters / switch dashboard scope to Project Overview.                               |
| Screenshots/video missing                                        | Run produced no artifacts (fast pass, headless config, or runner error). Check observer terminal logs; re-run headed once via browser settings if needed. |
| Import shows 0 requirements                                      | Excel headers unrecognized or text blocks <25 chars. Use the `RequirementId \| Requirement \| Priority \| Module` template.                               |
| Overlay stuck on Sign In                                         | Token expired. Re-login; check `sessionStorage.access_token` / backend auth logs.                                                                         |
| Only one execution runs at a time                                | Expected: queue is single-active. Wait for `Finished` before launching the next batch.                                                                    |

---

## 7. Done criteria for CricScore

- [ ] CricScore project selected; target URL verified as `https://cricscore-wheat.vercel.app/`.
- [ ] ≥2 requirements created/imported (homepage + scorecard minimum).
- [ ] Scenarios generated per requirement; ≥1 approved per requirement.
- [ ] Test cases generated; duplicates rejected; happy + negative paths approved.
- [ ] Playwright scripts generated for all approved cases (framework = `playwright`).
- [ ] ≥1 batch executed from **Run Selected Batch** via the workspace.
- [ ] Executions visible in history with screenshots/video; failures triaged.
- [ ] Reports show pass/total with no active-filter confusion.
- [ ] Dashboard Workflow Progress shows all steps `COMPLETED` for the covered requirements.
