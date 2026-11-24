# 0010. Bug Reporting Agent: reviewed bug report drafts before Jira

**Date**: 2026-11-23
**Status**: In Progress

## Summary

Before this change, two code paths filed Jira bugs and they overlapped:
- `DefectManagementAgent` looked only at the last result and used only the error message.
- `sync_execution_finished` filed a bug for every failed run, including flaky and environment failures.
- Batch runs never reached the graph's defect node.

This spec replaces both paths with one Bug Reporting Agent. The agent writes a structured bug report **draft** for each run triaged as a Product Bug, using all of the triage evidence. A person reviews and edits the draft in a Bug Reports view and submits it to Jira, where duplicate detection runs. A project can opt into auto-submit above a confidence threshold. The same flow is available from a **Report bug** button and from the chat assistant.

## Requirements

**User stories**:
- As a tester, I want each real product failure written up as a reproducible bug report, so I don't copy errors from logs by hand.
- As a QA lead, I want to review a bug before it reaches Jira, so flaky or environment failures don't spam the developers' backlog.
- As a developer, I want one Jira bug per defect, with steps, expected and actual result, evidence and the screenshot, linked to its Story.

**Acceptance criteria**:
- **AC-1**: Every failed or errored run triaged as `Product Bug` gets a bug report draft. This applies to graph runs (BugReportingAgent), batch runs and workspace webhook runs (`ExecutionService._triage`). Other categories get no draft unless someone asks for one (AC-7).
- **AC-2**: A draft holds:
  - title, summary, steps to reproduce, expected and actual result, severity (`critical`, `major`, `minor` or `trivial`) and priority (`highest`, `high`, `medium` or `low`)
  - failing step, root cause, evidence, confidence, flaky score and failure category, copied from triage
  - environment: target URL, framework, browser and run time
  - attachments (screenshot and trace) and the linked run

  An LLM failure falls back to a deterministic draft built from the test case and the error. Drafting never fails a run.
- **AC-3**: Vault credentials and test data values never appear in a report: they are replaced with `[redacted]`. This applies to drafts and to edits.
- **AC-4**: A repeat failure with the same error signature on the same test case adds an occurrence to the open report instead of creating a new one. If that report is already in Jira, the Jira issue gets a comment. A different error gets its own report.
- **AC-5**: Nothing is sent to Jira until someone submits the draft, unless the project sets `bug_auto_submit`. With auto-submit on, a draft is submitted at once when the triage confidence is at least `bug_auto_submit_min_confidence` (default 0.85).
- **AC-6**: Submitting a report does the following:
  1. Searches open bugs in the project's Jira key, first by the label `bugsig_<signature>`, then by LLM-chosen search terms. The JQL is built by the service, never by the LLM.
  2. If a duplicate is found, comments on it (`linked_duplicate`).
  3. Otherwise creates a Bug (`submitted`) labelled `platform_sync`, `bug`, `test_<id>`, `scenario_<id>` and `bugsig_<signature>`. It uploads the attachments and links the Bug to the scenario's Story.
  4. Writes the Jira key on every linked run and on the test case.

  A Jira error sets `submit_failed` with the reason and can be retried.
- **AC-7**: `POST /projects/{id}/executions/{eid}/bug-report` drafts a report for any failed or errored run, whatever its category. It returns the run's existing report if it has one, and returns 409 for a passed run.
- **AC-8**: Only `draft` and `submit_failed` reports can be edited, submitted or dismissed. Any other status returns 409. A report from another project returns 404.
- **AC-9**: GET routes need `Executions.view`; edit, submit, dismiss and create need `Executions.edit`. The user must also have project access.
- **AC-10**: When a Jira issue moves to a resolved status, the Jira webhook marks the reports that link it `resolved`.
- **AC-11**: `sync_execution_finished` no longer creates bugs, but still updates the Story. The `sync_bug` operation drafts (if needed), then submits through the same service.
- **AC-12**: The Bug Reports view has:
  - a list with status filters
  - a keyboard-reachable detail form with Save, Submit to Jira and Dismiss
  - a status badge and a Jira link

  The triage modal shows **Report bug** or **View bug report**. The Executions table links a run's bug report until it has a Jira key.
- **AC-13**: The chat assistant can:
  - read bug reports with `list_bug_reports` and `get_bug_report`
  - propose `report_bug` (draft for a failed execution) and `submit_bug_report`

  Both proposals are confirm-gated and use the `Executions.edit` permission.
- **AC-14**: Project settings expose the auto-submit toggle and threshold. The threshold must be between 0 and 1 (422 otherwise).

## Decision

**Chosen option**: one `BugReportService` called from four places: the graph agent, batch triage, the routes and chat. Reports are saved in the app as drafts and reviewed before Jira. Both earlier bug paths are removed.

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Implementation map

| Area | Files |
|---|---|
| Domain | `backend/models/bug_report.py`; `ExecutionResult.bug_report_id`; `WorkflowState.bug_report_ids`; `Project.bug_auto_submit*` |
| Persistence | `backend/repository/bug_report_repository.py` (JSON, in memory), `postgres_bug_report_repository.py`, `BugReportDB`, migration `c3f8a1d7e925` |
| Logic | `backend/services/bug_report_service.py`; prompts in `backend/prompts/agents/bug_reporting/` |
| Graph | `backend/agents/bug_reporting_agent.py`; `backend/graph/workflow.py` (`execution_analysis_agent → bug_reporting_agent → report_agent`) |
| Routes | `backend/main.py` (bug report routes, Jira webhook, project settings); `backend/schemas/bug_report_dto.py` |
| UI | `frontend/js/bug-reports.js`, `app.js` (`openBugReport`), `components.js` (Executions table), `index.html` |
| Chat | `backend/services/chat_actions.py`, `chat_tools.py`, `frontend/js/chat.js` |
| Tests | `backend/tests/test_bug_reports.py`, `test_chat_bug_reports.py`, `backend/e2e/bug-reports.spec.ts` |
