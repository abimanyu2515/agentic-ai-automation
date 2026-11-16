# App guide

<!-- Hand written from docs/workflow.md and the screens in frontend/index.html. Keep it in step with them by hand, and under CHAT_GUIDE_CHARS (default 8000 characters). Comments are stripped before the guide goes to the model. -->

## What the app does

The platform turns requirements into automated tests. The usual flow for a project is:

1. **Create a project** with a target URL (the application under test) and a framework. Login credentials for the target app are stored encrypted in the vault, never shown in plain text.
2. **Add requirements** on the Requirements screen, by typing them in or uploading a document.
3. **Generate scenarios** from a requirement. Scenarios are high level test ideas.
4. **Review scenarios** when human review is on. Approved scenarios move on; rejected ones are skipped.
5. **Generate test cases** from approved scenarios. Each test case has steps, expected results, and test data. An evaluation step scores each one; low relevance cases are rejected, others can go to review.
6. **Generate a Playwright script** for an approved test case on the Playwright screen. Script generation runs in the background; the screen shows when it finishes.
7. **Run** a test case or a batch of test cases. Executions stream their progress live, and each run saves its result, screenshots, video, and an analysis of failures.
8. **Review results** on the Executions and Reports screens, and sync stories or bugs to Jira when Jira is set up.

## Screens

- **Dashboard**: project metrics such as pass rate, counts, and recent activity. Admins without a project see all projects.
- **Project**: the selected project's details and settings, including the target URL, framework, credentials, and whether the target is a safe test environment.
- **Requirements**: list, add, edit, and delete requirements. Generate scenarios from here.
- **Scenarios**: the scenarios of each requirement, with approval when human review is on. Edit, duplicate, or delete scenarios.
- **Test Cases**: test cases per scenario with steps, expected results, and test data. Approve or reject, edit, and generate test data. The assistant can also propose approving or rejecting a test case, and generating its test data (20 datasets, Playwright test cases only).
- **Object Repository**: named pages and elements with their locators, collected from the target app. Scripts refer to elements by key instead of raw selectors, so a changed locator is fixed in one place.
- **Jira Traceability**: links between requirements, test cases, and Jira issues.
- **Playwright**: view and generate scripts for a test case, and open the execution workspace.
- **Executions**: every test run with its status (passed, failed, error), duration, error message, and report. Failed runs can be deleted one by one or all at once.
- **Reports**: execution reports and summaries.
- **LangSmith**: LLM call traces and cost estimates when tracing is on.
- **Settings**: LLM settings (read only role settings come from the server `.env`), users, roles and permissions, audit logs, and external tools such as Jira.

## Useful facts

- A project's target URL always comes from the project. There is no global application URL.
- Grounded script validation inspects the real target app before generating scripts. It only runs when the project is marked as a safe test environment and the user confirms the batch.
- A script is ready only when its validation state is `validated`. Failed and unverified scripts stay visible for review.
- The object repository learns from runs: each run records which locator worked, and elements that keep failing are flagged.
- Permissions are per role and can differ per project. If a button is missing, the user's role may not allow that action.
- The LLM roles are `generation` (test data and scripts), `analysis` (everything else), and `chat` (this assistant).

## Chat assistant

- The chat button is at the bottom right of every screen. Conversations are saved per user per project; use "New chat" to start fresh and the thread list to go back to an old one.
- Threads with no activity for 90 days are deleted.
- The assistant can propose an action (so far: generating scenarios for a requirement). It shows as a card with Run and Cancel, and nothing happens until the user clicks Run. A card that replaces existing scenarios lists them and needs DELETE typed first. Sending a new message expires a card that was not run.
