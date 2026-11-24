# Rationale · spec 0010 Bug Reporting Agent

## Problem

- **Two paths filed Jira bugs.** `DefectManagementAgent` ran inside the graph. `sync_execution_finished` ran after every run, so most failed graph runs could produce two bugs.
- **The second path filed every failure.** It created a bug for any failed run, so flaky tests and environment outages reached the developers' backlog.
- **The graph agent used little of the triage.** It looked only at `execution_results[-1]` and sent only `error_message` to the LLM. It ignored the evidence, failing step, root cause and confidence that `LogAnalysisService` now produces.
- **Batch runs never got a bug.** They skip the graph entirely.

## Options considered

1. **Upgrade `DefectManagementAgent` in place and keep `sync_execution_finished`.** This is the least code, but duplicate bugs remain and batch runs are still not covered.
2. **Add a new agent next to the existing paths.** This keeps the old behavior, but adds a third bug writer and more duplicates.
3. **Replace both paths with one service plus a thin graph agent (chosen).** Every caller (graph, batch, routes, chat) goes through the same rules: draft, dedupe, redaction, duplicate search and submit.

## Filing mode

- **Auto-file in Jira.** Fast, but triage confidence is a model estimate. A wrong `Product Bug` is costly, and once a Jira issue exists it is public to the team.
- **Draft only, no Jira.** Safe, but leaves the last step manual.
- **Draft, then a person approves (chosen).** A per-project auto-submit above a threshold keeps the fast path open for teams that trust the triage. It is off by default.

## Design notes

- **Dedupe in two layers.** In the app, the open report is keyed by `run_history.error_signature`, which already ignores ids, numbers and quoted values. That way repeated runs collect occurrences instead of drafts. In Jira, the `bugsig_` label finds our own earlier bugs exactly, and LLM search terms find bugs that people filed by hand.
- **The service writes the JQL.** The LLM supplies only short search terms, which are quoted and escaped. The old agent ran LLM-written JQL as is, which could search outside the project or fail on syntax.
- **Reports live in their own store.** That follows the chat and token usage pattern, with JSON by default and PostgreSQL optional. The table keeps lookup columns plus a JSON payload, so new report fields need no migration.
- **Execution ids can change after the graph.** A queued single run gets its final id after the graph returns, so `relink_execution` moves the report to the final id.
- **No outbound call in tests.** An autouse fixture gives every test an in-memory store and an LLM that always fails, so drafting takes the deterministic fallback. `JiraService` is already in mock mode under pytest.

## Consequences

- `sync_execution_finished` no longer links a bug to the test case on failure. The test case gets its Jira key when its report is submitted.
- `DefectManagementAgent` and its prompt are deleted. Old token usage rows keep their `defect_management_agent` label.
