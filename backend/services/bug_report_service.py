"""
Bug report drafting and Jira submission.

`BugReportService` is the one place that turns a triaged failure into a bug
report and files it in Jira. BugReportingAgent (graph runs), ExecutionService
(batch and webhook runs), the API routes and the chat assistant all call it.

Flow
1. `draft_from_execution` writes a draft for a "Product Bug" failure (any
   failure when `force=True`). The same error signature on the same test case
   reuses the open report and records the run as another occurrence.
2. A person reviews and edits the draft (`update`), then `submit` searches Jira
   for a duplicate and either comments on it or creates a new Bug.
3. A project may opt into auto-submit above a confidence threshold.

Drafting never raises: an LLM failure falls back to a deterministic draft.
`submit` records Jira failures on the report (`submit_failed`) instead of
raising. Every text field is redacted against the project's vault
credentials and test data before it is saved.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.accessibility import AccessibilityReport, AccessibilityReportStatus, FindingKind
from backend.models.bug_report import (
    ACCESSIBILITY_CATEGORY,
    EDITABLE_STATUSES,
    SEVERITIES,
    BugOccurrence,
    BugReport,
    BugReportStatus,
)
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.services import run_history
from backend.services.accessibility_service import (
    BLOCK_SEVERITIES,
    DEFAULT_BLOCK_SEVERITY,
    _at_or_above,
    screenshot_file,
)
from backend.services.redaction import clean, secret_patterns
from backend.utils.prompts import load_prompt

logger = logging.getLogger(__name__)

PRODUCT_BUG = "Product Bug"
DRAFT_PROMPT = "agents/bug_reporting/roles_and_responsibilities.md"
DUPLICATE_PROMPT = "agents/bug_reporting/duplicate_search.md"
PRIORITIES = ("highest", "high", "medium", "low")
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
EDITABLE_FIELDS = (
    "title", "summary", "steps_to_reproduce", "expected_result", "actual_result", "severity", "priority",
)


class BugReportError(ValueError):
    """A request the report's current state does not allow (the API maps it to 409)."""


class _BugReportDraft(BaseModel):
    title: str = Field(description="At most 120 characters; names the broken behavior")
    summary: str
    steps_to_reproduce: list[str]
    expected_result: str
    actual_result: str
    severity: str = Field(description="critical, major, minor or trivial")
    priority: str = Field(description="highest, high, medium or low")


class _DuplicateSearch(BaseModel):
    search_terms: list[str] = Field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _status_value(status) -> str:
    return getattr(status, "value", status)


def _block(items, empty: str) -> str:
    """Evidence goes inside XML-like tags; keep it from closing them early."""
    text = "\n".join(f"- {item}" for item in items) if isinstance(items, list) else items
    text = (str(text) if text is not None else "").strip() or empty
    return text.replace("</", "< /")


def _first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def _label_text(value: str) -> str:
    """Jira labels cannot hold spaces."""
    return re.sub(r"\s+", "_", str(value).strip())


def _jql_text(value: str) -> str:
    """Quote a value for a JQL string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class BugReportService:
    """Inject `llm_service`, `bug_repo`, `project_repo`, `jira_service`, `vault` and
    `accessibility_repo` in tests."""

    def __init__(
        self, llm_service=None, bug_repo=None, project_repo=None, jira_service=None, vault=None,
        accessibility_repo=None,
    ):
        self._accessibility_repo = accessibility_repo
        self._llm_service = llm_service
        self._bug_repo = bug_repo
        self._project_repo = project_repo
        self._jira_service = jira_service
        self._vault = vault

    # ---------------------------------------------------------------- dependencies

    @property
    def llm_service(self):
        if self._llm_service is None:
            from backend.services.llm import LLMService

            self._llm_service = LLMService(role="analysis", agent="bug_reporting_agent")
        return self._llm_service

    @property
    def bug_repo(self):
        if self._bug_repo is None:
            from backend.repository.bug_report_repository import get_bug_report_repository

            self._bug_repo = get_bug_report_repository()
        return self._bug_repo

    @property
    def project_repo(self):
        if self._project_repo is None:
            from backend.repository.project_repository import get_project_repository

            self._project_repo = get_project_repository()
        return self._project_repo

    @property
    def jira_service(self):
        if self._jira_service is None:
            from backend.services.jira_service import JiraService

            self._jira_service = JiraService()
        return self._jira_service

    # ---------------------------------------------------------------- queries

    def list_reports(self, project_id: UUID, status: BugReportStatus | None = None) -> list[BugReport]:
        return self.bug_repo.list_reports(project_id, status)

    def get(self, project_id: UUID, report_id: UUID) -> BugReport | None:
        report = self.bug_repo.get(report_id)
        return report if report and report.project_id == project_id else None

    # ---------------------------------------------------------------- drafting

    def draft_from_execution(
        self,
        project_id: UUID,
        result: ExecutionResult,
        test_case=None,
        retrieved_context: list[dict] | None = None,
        force: bool = False,
        actor: str = "agent",
    ) -> BugReport | None:
        """Draft (or reuse) the report for one failed run and set `result.bug_report_id`.

        Returns None when the run is not a reportable failure. The caller saves
        `result`. Never raises.
        """
        try:
            return self._draft(project_id, result, test_case, retrieved_context, force, actor)
        except Exception:
            logger.exception("Bug report drafting failed: execution_id=%s", result.id)
            return None

    def report_for_execution(self, project_id: UUID, execution_id: UUID, actor: str) -> BugReport:
        """On-demand report from the UI or chat: drafts for any failed run, then saves the run."""
        result = self.project_repo.get_execution_result(project_id, execution_id)
        if result is None:
            raise LookupError("Execution not found")
        if _status_value(result.status) not in (ExecutionStatus.FAILED.value, ExecutionStatus.ERROR.value):
            raise BugReportError("Only a failed run can be reported as a bug")
        report = self._draft(project_id, result, None, None, force=True, actor=actor)
        if report is None:
            raise BugReportError("Could not draft a bug report for this run")
        self.project_repo.save_execution_result(project_id, result)
        return report

    def relink_execution(self, report_id: UUID, old_id: UUID, new_id: UUID) -> None:
        """A run's id can be replaced after the graph finishes (queued executions keep
        the id the client was given). Point the report at the final id."""
        if old_id == new_id:
            return
        report = self.bug_repo.get(report_id)
        if report is None:
            return
        if report.execution_id == old_id:
            report.execution_id = new_id
        for occurrence in report.occurrences:
            if occurrence.execution_id == old_id:
                occurrence.execution_id = new_id
        report.touch()
        self.bug_repo.save(report)

    # ---------------------------------------------------------------- accessibility (spec 0011)

    @property
    def accessibility_repo(self):
        if self._accessibility_repo is None:
            from backend.repository.accessibility_repository import get_accessibility_repository

            self._accessibility_repo = get_accessibility_repository()
        return self._accessibility_repo

    def draft_from_accessibility(
        self,
        project_id: UUID,
        a11y_report: AccessibilityReport,
        result: ExecutionResult | None = None,
        test_case=None,
        actor: str = "agent",
    ) -> list[BugReport]:
        """Draft (or reuse) one bug per violation at or above the project's
        `a11y_block_severity` (AC-6), and set each finding's `bug_report_id`.

        One open bug covers one finding signature per project. A repeat adds an
        occurrence; a dismissed bug ignores repeats; a resolved bug gets a new
        draft. `needs_review` findings never create bugs. Saves the report's
        updated findings. Never raises.
        """
        try:
            return self._draft_accessibility(project_id, a11y_report, result, test_case, actor)
        except Exception:
            logger.exception("Accessibility bug drafting failed: report_id=%s", a11y_report.id)
            return []

    def _draft_accessibility(self, project_id, a11y_report, result, test_case, actor) -> list[BugReport]:
        if a11y_report.status != AccessibilityReportStatus.SUCCEEDED:
            return []
        project = self._safe(lambda: self.project_repo.get_project(project_id))
        threshold = getattr(project, "a11y_block_severity", None)
        if threshold not in BLOCK_SEVERITIES:
            threshold = DEFAULT_BLOCK_SEVERITY
        findings = [
            f for f in a11y_report.findings
            if f.kind == FindingKind.VIOLATION and _at_or_above(f.impact, threshold)
        ]
        if not findings:
            return []
        if test_case is None and a11y_report.test_case_id is not None:
            test_case = self._safe(lambda: self.project_repo.get_test_case(a11y_report.test_case_id))
        patterns = secret_patterns(project_id, test_case, self._vault)

        reports: list[BugReport] = []
        created: list[BugReport] = []
        changed = False
        for finding in findings:
            report, is_new = self._accessibility_bug(project_id, a11y_report, finding, result, test_case, patterns, actor)
            if report is None:
                continue
            if finding.bug_report_id != report.id:
                finding.bug_report_id = report.id
                changed = True
            reports.append(report)
            if is_new:
                created.append(report)
        if changed:
            self._safe(lambda: self.accessibility_repo.save(a11y_report))

        # AC-7: the project's bug_auto_submit setting decides, as for functional bugs.
        if created and getattr(project, "bug_auto_submit", False):
            submitted = {r.id: self.submit(project_id, r.id, actor="auto") for r in created}
            reports = [submitted.get(r.id, r) for r in reports]
        return reports

    def _accessibility_bug(self, project_id, a11y_report, finding, result, test_case, patterns, actor):
        """Returns (report, created). (None, False) when a dismissed bug ignores the repeat."""
        occurrence = BugOccurrence(
            execution_id=a11y_report.execution_id,
            accessibility_report_id=a11y_report.id,
            executed_at=a11y_report.finished_at or a11y_report.created_at,
        )
        open_report = self.bug_repo.find_open_a11y_by_signature(project_id, finding.signature)
        if open_report is not None:
            return self._add_accessibility_occurrence(open_report, occurrence), False
        latest = self.bug_repo.find_latest_a11y_by_signature(project_id, finding.signature)
        if latest is not None and latest.status == BugReportStatus.DISMISSED:
            return None, False

        page = finding.pages[0] if finding.pages else ""
        scenario_id = getattr(test_case, "scenario_id", None)
        scenario = self._safe(lambda: self.project_repo.get_scenario(scenario_id)) if scenario_id else None
        shot = screenshot_file(a11y_report, finding.screenshot_path) if finding.screenshot_path else None
        evidence = [f"Element: {finding.selector}"] if finding.selector else []
        if finding.html_snippet:
            evidence.append(f"HTML: {finding.html_snippet}")
        if finding.pages:
            evidence.append("Pages: " + ", ".join(finding.pages))
        if finding.help_url:
            evidence.append(f"Rule details: {finding.help_url}")

        report = BugReport(
            project_id=project_id,
            execution_id=a11y_report.execution_id,
            test_case_id=a11y_report.test_case_id,
            accessibility_report_id=a11y_report.id,
            scenario_id=scenario_id,
            requirement_id=getattr(scenario, "requirement_id", None),
            environment=self._accessibility_environment(project_id, page, result),
            root_cause=clean(finding.ai_explanation, patterns) or None,
            evidence=[clean(line, patterns) for line in evidence],
            failure_category=ACCESSIBILITY_CATEGORY,
            a11y_rule_id=finding.rule_id,
            wcag_criteria=list(finding.wcag_criteria),
            attachments=[str(shot)] if shot else [],
            error_signature=finding.signature,
            occurrences=[occurrence],
            created_by=actor,
            **self._accessibility_content(finding, page, test_case, patterns),
        )
        self.bug_repo.save(report)
        logger.info(
            "Accessibility bug drafted: report_id=%s rule=%s a11y_report_id=%s",
            report.id, finding.rule_id, a11y_report.id,
        )
        return report, True

    def _add_accessibility_occurrence(self, report: BugReport, occurrence: BugOccurrence) -> BugReport:
        # A retried webhook replaces the accessibility report under the same id,
        # so it never counts twice.
        if all(o.accessibility_report_id != occurrence.accessibility_report_id for o in report.occurrences):
            report.occurrences.append(occurrence)
            report.touch()
            self.bug_repo.save(report)
            key = report.jira_key or report.duplicate_of_jira_key
            if key:
                self._safe(lambda: self.jira_service.add_comment(
                    key,
                    f"The same accessibility problem was found again at {occurrence.executed_at.isoformat()} "
                    f"(occurrence {len(report.occurrences)}).",
                ))
        logger.info("Accessibility bug reused: report_id=%s", report.id)
        return report

    def _accessibility_content(self, finding, page: str, test_case, patterns) -> dict:
        """Deterministic text: the finding already carries the AI explanation and fix."""
        rule = finding.help or finding.rule_id
        # AC-7: a bug with no test case uses the page URL as its title context.
        context = getattr(test_case, "title", None) or page or "unknown page"
        criteria = ", ".join(f"WCAG {c}" for c in finding.wcag_criteria) or "WCAG"
        summary = finding.ai_explanation or rule
        if finding.ai_fix:
            summary = f"{summary}\n\nSuggested fix:\n{finding.ai_fix}"
        steps = [f"Open {page}" if page else "Open the page named in the evidence"]
        if test_case is not None and getattr(test_case, "title", None):
            steps.insert(0, f"Run the test case \"{test_case.title}\" or go straight to the page")
        if finding.selector:
            steps.append(f"Find the element {finding.selector}")
        steps.append(f"Check it against {criteria} (axe rule {finding.rule_id})")
        severity = "critical" if finding.impact == "critical" else "major"
        return self._normalize({
            "title": f"Accessibility: {rule} ({context})",
            "summary": summary,
            "steps_to_reproduce": steps,
            "expected_result": f"The element meets {criteria}: {rule}.",
            "actual_result": finding.failure_summary or rule,
            "severity": severity,
            "priority": "high" if severity == "critical" else "medium",
        }, patterns)

    def _accessibility_environment(self, project_id: UUID, page: str, result: ExecutionResult | None) -> dict[str, str]:
        if result is not None:
            env = self._environment(result)
        else:
            project = self._safe(lambda: self.project_repo.get_project(project_id))
            env = {k: str(getattr(project, k)) for k in ("target_url", "framework") if getattr(project, k, None)}
        if page:
            env["page_url"] = page
        return env

    def _draft(self, project_id, result, test_case, retrieved_context, force, actor) -> BugReport | None:
        status = _status_value(result.status)
        if status not in (ExecutionStatus.FAILED.value, ExecutionStatus.ERROR.value):
            return None
        if not force and result.failure_category != PRODUCT_BUG:
            return None

        existing = self.bug_repo.get_by_execution(project_id, result.id)
        if existing is not None:
            result.bug_report_id = existing.id
            return existing

        test_case = test_case or self._safe(lambda: self.project_repo.get_test_case(result.test_case_id))
        signature = run_history.error_signature(result.error_message or result.root_cause_summary)
        occurrence = BugOccurrence(execution_id=result.id, executed_at=result.executed_at)

        open_report = self.bug_repo.find_open_by_signature(project_id, result.test_case_id, signature)
        if open_report is not None:
            return self._add_occurrence(open_report, result, occurrence)

        patterns = secret_patterns(project_id, test_case, self._vault)
        content = self._generate(result, test_case, retrieved_context, patterns)
        scenario_id = getattr(test_case, "scenario_id", None)
        scenario = self._safe(lambda: self.project_repo.get_scenario(scenario_id)) if scenario_id else None

        report = BugReport(
            project_id=project_id,
            execution_id=result.id,
            test_case_id=result.test_case_id,
            scenario_id=scenario_id,
            requirement_id=getattr(scenario, "requirement_id", None),
            environment=self._environment(result),
            failing_step=clean(result.failing_step, patterns) or None,
            root_cause=clean(result.root_cause_summary, patterns) or None,
            evidence=[clean(line, patterns) for line in (result.evidence or [])],
            confidence=result.confidence,
            flaky_score=result.flaky_score,
            failure_category=result.failure_category,
            attachments=[p for p in (result.screenshot_path, result.trace_path) if p],
            error_signature=signature,
            occurrences=[occurrence],
            created_by=actor,
            **content,
        )
        self.bug_repo.save(report)
        result.bug_report_id = report.id
        logger.info("Bug report drafted: report_id=%s execution_id=%s", report.id, result.id)

        if not force and self._auto_submit_allowed(project_id, result):
            report = self.submit(project_id, report.id, actor="auto")
            key = report.jira_key or report.duplicate_of_jira_key
            if key:
                # The caller saves `result`, which may not be in the repository yet.
                result.jira_bug_id, result.jira_bug_url = key, report.jira_url
        return report

    def _add_occurrence(self, report: BugReport, result: ExecutionResult, occurrence: BugOccurrence) -> BugReport:
        if all(o.execution_id != occurrence.execution_id for o in report.occurrences):
            report.occurrences.append(occurrence)
        report.touch()
        self.bug_repo.save(report)
        result.bug_report_id = report.id
        key = report.jira_key or report.duplicate_of_jira_key
        if key:
            result.jira_bug_id = key
            result.jira_bug_url = report.jira_url or self._browse_url(key)
            self._safe(lambda: self.jira_service.add_comment(
                key,
                f"The same failure happened again at {result.executed_at.isoformat()} "
                f"(occurrence {len(report.occurrences)}).",
            ))
        logger.info("Bug report reused: report_id=%s execution_id=%s", report.id, result.id)
        return report

    def _generate(self, result, test_case, retrieved_context, patterns) -> dict:
        steps = list(getattr(test_case, "steps", None) or [])
        try:
            from backend.agents.knowledge_context import with_project_knowledge

            prompt = load_prompt(
                DRAFT_PROMPT,
                test_title=_block(getattr(test_case, "title", None), "Unknown test case"),
                steps=_block(steps, "N/A"),
                expected_result=_block(getattr(test_case, "expected_result", None), "N/A"),
                failure_category=result.failure_category or "Unclassified",
                confidence=f"{result.confidence:.2f}" if result.confidence is not None else "unknown",
                failing_step=_block(result.failing_step, "Unknown"),
                root_cause=_block(result.root_cause_summary, "Not analysed"),
                error_message=_block(result.error_message, "No error message captured"),
                evidence=_block(list(result.evidence or []), "None captured"),
                frontend_errors=_block(result.frontend_error_details, "None detected"),
                environment=_block([f"{k}: {v}" for k, v in self._environment(result).items()], "Unknown"),
                run_history=_block(self._history_text(result), "No earlier runs"),
            )
            prompt = with_project_knowledge(clean(prompt, patterns), retrieved_context)
            draft = self.llm_service.structured_generate(user=prompt, response_model=_BugReportDraft)
            content = {
                "title": draft.title,
                "summary": draft.summary,
                "steps_to_reproduce": [s for s in draft.steps_to_reproduce if s and s.strip()] or steps,
                "expected_result": draft.expected_result,
                "actual_result": draft.actual_result,
                "severity": draft.severity,
                "priority": draft.priority,
            }
        except Exception as exc:
            logger.warning("Bug report LLM draft failed, using the fallback: %s", exc)
            content = self._fallback(result, test_case, steps)
        return self._normalize(content, patterns)

    @staticmethod
    def _fallback(result, test_case, steps) -> dict:
        title = getattr(test_case, "title", None) or "Unknown test case"
        error = _first_line(result.error_message) or _first_line(result.root_cause_summary) or "Test failed"
        return {
            "title": f"[{title}] {error}",
            "summary": result.root_cause_summary or result.error_message or "",
            "steps_to_reproduce": steps,
            "expected_result": getattr(test_case, "expected_result", None) or "",
            "actual_result": result.root_cause_summary or result.error_message or "",
            "severity": "major",
            "priority": "medium",
        }

    def _normalize(self, content: dict, patterns) -> dict:
        severity = str(content.get("severity") or "").strip().lower()
        priority = str(content.get("priority") or "").strip().lower()
        title = clean(content.get("title"), patterns).strip() or "Untitled bug"
        return {
            "title": title[:255],
            "summary": clean(content.get("summary"), patterns),
            "steps_to_reproduce": [clean(s, patterns) for s in content.get("steps_to_reproduce") or []],
            "expected_result": clean(content.get("expected_result"), patterns),
            "actual_result": clean(content.get("actual_result"), patterns),
            "severity": severity if severity in SEVERITIES else "major",
            "priority": priority if priority in PRIORITIES else "medium",
        }

    @staticmethod
    def _environment(result: ExecutionResult) -> dict[str, str]:
        snapshot = result.project_config_snapshot or {}
        env = {key: str(snapshot[key]) for key in ("target_url", "framework") if snapshot.get(key)}
        if result.browser_version:
            env["browser"] = result.browser_version
        env["executed_at"] = result.executed_at.isoformat()
        return env

    @staticmethod
    def _history_text(result: ExecutionResult) -> str:
        history = result.run_history_summary or {}
        if not history.get("runs"):
            return ""
        return (
            f"{history.get('runs')} earlier runs: {history.get('passes', 0)} passed, "
            f"{history.get('fails', 0)} failed, {history.get('same_error_count', 0)} with the same error; "
            f"flaky score {history.get('flaky_score', 0)}"
        )

    def _auto_submit_allowed(self, project_id: UUID, result: ExecutionResult) -> bool:
        project = self._safe(lambda: self.project_repo.get_project(project_id))
        if not project or not getattr(project, "bug_auto_submit", False):
            return False
        threshold = getattr(project, "bug_auto_submit_min_confidence", 0.85)
        return result.confidence is not None and result.confidence >= threshold

    # ---------------------------------------------------------------- redaction

    # ---------------------------------------------------------------- edits

    def update(self, project_id: UUID, report_id: UUID, patch: dict, actor: str) -> BugReport:
        report = self._require(project_id, report_id)
        if report.status not in EDITABLE_STATUSES:
            raise BugReportError(f"A {report.status.value} bug report cannot be edited")
        test_case = (
            self._safe(lambda: self.project_repo.get_test_case(report.test_case_id))
            if report.test_case_id else None
        )
        patterns = secret_patterns(project_id, test_case, self._vault)
        merged = {field: getattr(report, field) for field in EDITABLE_FIELDS}
        merged.update({k: v for k, v in patch.items() if k in EDITABLE_FIELDS and v is not None})
        for field, value in self._normalize(merged, patterns).items():
            setattr(report, field, value)
        report.touch()
        self.bug_repo.save(report)
        logger.info("Bug report edited: report_id=%s by=%s", report.id, actor)
        return report

    def dismiss(self, project_id: UUID, report_id: UUID, reason: str, actor: str) -> BugReport:
        report = self._require(project_id, report_id)
        if report.status not in EDITABLE_STATUSES:
            raise BugReportError(f"A {report.status.value} bug report cannot be dismissed")
        report.status = BugReportStatus.DISMISSED
        report.dismiss_reason = (reason or "").strip()[:500] or None
        report.touch()
        self.bug_repo.save(report)
        logger.info("Bug report dismissed: report_id=%s by=%s", report.id, actor)
        return report

    def mark_resolved_by_jira_key(self, jira_key: str) -> int:
        count = 0
        for report in self.bug_repo.list_by_jira_key(jira_key):
            if report.status in (BugReportStatus.SUBMITTED, BugReportStatus.LINKED_DUPLICATE):
                report.status = BugReportStatus.RESOLVED
                report.touch()
                self.bug_repo.save(report)
                count += 1
        return count

    # ---------------------------------------------------------------- submission

    def submit(self, project_id: UUID, report_id: UUID, actor: str) -> BugReport:
        """File the report in Jira. Jira failures are stored on the report, not raised."""
        report = self._require(project_id, report_id)
        if report.status not in EDITABLE_STATUSES:
            raise BugReportError(f"A {report.status.value} bug report cannot be submitted")

        project = self._safe(lambda: self.project_repo.get_project(project_id))
        project_key = getattr(project, "jira_project_key", None) or self.jira_service.config.project_key or "QA"
        report.submitted_by = actor
        try:
            duplicate = self._find_duplicate(report, project_key)
            if duplicate:
                key = duplicate["key"]
                self.jira_service.add_comment(key, self._duplicate_comment(report))
                report.status = BugReportStatus.LINKED_DUPLICATE
                report.duplicate_of_jira_key = key
                report.jira_url = self._browse_url(key)
                linked_key, sync_status = key, "linked"
            else:
                issue = self.jira_service.create_issue(
                    summary=report.title,
                    description=self._description(report),
                    issue_type="Bug",
                    project_key=project_key,
                    labels=self._labels(report),
                )
                if not issue or not issue.get("key"):
                    raise RuntimeError("Jira did not create the issue")
                report.jira_key = issue["key"]
                report.jira_url = issue.get("url") or self._browse_url(issue["key"])
                report.status = BugReportStatus.SUBMITTED
                self._attach(report)
                self._link_story(report)
                linked_key, sync_status = report.jira_key, "created"
            report.submit_error = None
        except Exception as exc:
            logger.warning("Bug report submit failed: report_id=%s error=%s", report.id, exc)
            report.status = BugReportStatus.SUBMIT_FAILED
            report.submit_error = str(exc)[:500]
            report.touch()
            self.bug_repo.save(report)
            return report

        report.touch()
        self.bug_repo.save(report)
        if not report.is_accessibility:
            # A run's jira_bug_id and the test case's Jira link belong to its
            # functional bug; an accessibility bug links through the finding.
            self._link_runs(project_id, report, linked_key, report.jira_url, sync_status)
        self._safe(lambda: self._queue_defect(project_id, linked_key))
        logger.info("Bug report submitted: report_id=%s jira=%s status=%s", report.id, linked_key, report.status.value)
        return report

    def _find_duplicate(self, report: BugReport, project_key: str) -> dict | None:
        base = (
            f'project = "{_jql_text(project_key)}" AND issuetype = Bug AND statusCategory != Done'
        )
        if report.error_signature:
            hits = self.jira_service.search_issues(f'{base} AND labels = "{self._signature_label(report)}"')
            if hits:
                return hits[0]
        terms = self._search_terms(report)
        if not terms:
            return None
        clause = " OR ".join(f'text ~ "\\"{_jql_text(term)}\\""' for term in terms)
        hits = self.jira_service.search_issues(f"{base} AND ({clause}) ORDER BY updated DESC")
        return hits[0] if hits else None

    def _search_terms(self, report: BugReport) -> list[str]:
        try:
            prompt = load_prompt(
                DUPLICATE_PROMPT,
                title=_block(report.title, "N/A"),
                summary=_block(report.summary, "N/A"),
                actual_result=_block(report.actual_result, "N/A"),
                failing_step=_block(report.failing_step, "N/A"),
            )
            response = self.llm_service.structured_generate(user=prompt, response_model=_DuplicateSearch)
        except Exception as exc:
            logger.warning("Bug duplicate search terms failed: %s", exc)
            return []
        terms = []
        for term in response.search_terms or []:
            term = re.sub(r"\s+", " ", str(term)).strip()
            if 2 < len(term) <= 60 and term.lower() not in (t.lower() for t in terms):
                terms.append(term)
        return terms[:5]

    @staticmethod
    def _signature_label(report: BugReport) -> str:
        return f"bugsig_{report.error_signature[:12]}"

    def _labels(self, report: BugReport) -> list[str]:
        labels = ["platform_sync", "bug"]
        if report.is_accessibility:
            # AC-7: accessibility, a11y_<rule_id>, and wcag_<criterion> for each criterion.
            labels.append("accessibility")
            if report.a11y_rule_id:
                labels.append(f"a11y_{_label_text(report.a11y_rule_id)}")
            labels += [f"wcag_{_label_text(c)}" for c in report.wcag_criteria]
        if report.test_case_id:
            labels.append(f"test_{report.test_case_id}")
        if report.scenario_id:
            labels.append(f"scenario_{report.scenario_id}")
        if report.error_signature:
            labels.append(self._signature_label(report))
        return labels

    @staticmethod
    def _description(report: BugReport) -> str:
        lines = [report.summary or "", "", "*Steps to reproduce*"]
        lines += [f"# {step}" for step in report.steps_to_reproduce] or ["N/A"]
        lines += ["", "*Expected result*", report.expected_result or "N/A"]
        lines += ["", "*Actual result*", report.actual_result or "N/A"]
        if report.failing_step:
            lines += ["", f"*Failing step:* {report.failing_step}"]
        if report.root_cause:
            lines += ["", "*Root cause analysis*", report.root_cause]
        if report.evidence:
            lines += ["", "*Evidence*", "{noformat}", *report.evidence, "{noformat}"]
        if report.environment:
            lines += ["", "*Environment*"] + [f"* {k}: {v}" for k, v in report.environment.items()]
        details = [f"Severity: {report.severity}", f"Priority: {report.priority}"]
        if report.confidence is not None:
            details.append(f"Triage confidence: {report.confidence:.0%}")
        if report.flaky_score is not None:
            details.append(f"Flaky score: {report.flaky_score:.2f}")
        if report.a11y_rule_id:
            details.append(f"Accessibility rule: {report.a11y_rule_id}")
        if report.wcag_criteria:
            details.append("WCAG: " + ", ".join(report.wcag_criteria))
        lines += ["", " | ".join(details)]
        if report.execution_id:
            lines.append(f"Execution: {report.execution_id}")
        if report.accessibility_report_id:
            lines.append(f"Accessibility report: {report.accessibility_report_id}")
        if report.is_accessibility:
            lines.append("An automated scan does not certify WCAG conformance.")
        lines.append("Reported by the QA platform Bug Reporting Agent.")
        return "\n".join(lines)

    @staticmethod
    def _duplicate_comment(report: BugReport) -> str:
        source = (
            f"Execution: {report.execution_id}" if report.execution_id
            else f"Accessibility report: {report.accessibility_report_id}"
        )
        return (
            "The QA platform found this failure again.\n"
            f"{source}\n"
            f"Occurrences: {len(report.occurrences)}\n"
            f"Actual result: {report.actual_result or 'N/A'}"
        )

    def _attach(self, report: BugReport) -> None:
        for path in report.attachments:
            try:
                if not path or not os.path.isfile(path) or os.path.getsize(path) > MAX_ATTACHMENT_BYTES:
                    continue
                name = os.path.basename(path)
                mime = "image/png" if name.lower().endswith(".png") else "application/zip" if name.lower().endswith(".zip") else "application/octet-stream"
                with open(path, "rb") as handle:
                    self.jira_service.upload_attachment(report.jira_key, name, handle.read(), mime)
            except Exception as exc:
                logger.warning("Bug attachment upload failed: report_id=%s file=%s error=%s", report.id, path, exc)

    def _link_story(self, report: BugReport) -> None:
        if not report.scenario_id:
            return
        scenario = self._safe(lambda: self.project_repo.get_scenario(report.scenario_id))
        story_key = getattr(scenario, "jira_issue_key", None)
        if story_key:
            self._safe(lambda: self.jira_service.link_issues(report.jira_key, story_key, "Relates"))

    def _link_runs(self, project_id: UUID, report: BugReport, key: str, url: str | None, sync_status: str) -> None:
        execution_ids = dict.fromkeys(
            e for e in [report.execution_id, *(o.execution_id for o in report.occurrences)] if e is not None
        )
        for execution_id in execution_ids:
            def save(execution_id=execution_id):
                result = self.project_repo.get_execution_result(project_id, execution_id)
                if result:
                    result.jira_bug_id, result.jira_bug_url, result.bug_report_id = key, url, report.id
                    self.project_repo.save_execution_result(project_id, result)
            self._safe(save)

        def save_test_case():
            if report.test_case_id is None:
                return
            test_case = self.project_repo.get_test_case(report.test_case_id)
            if test_case:
                test_case.jira_issue_key = key
                test_case.jira_issue_url = url
                test_case.jira_sync_status = sync_status
                test_case.jira_last_synced_at = _utc_now()
                self.project_repo.update_test_case(test_case)
        self._safe(save_test_case)

    @staticmethod
    def _queue_defect(project_id: UUID, key: str) -> None:
        from backend.services.knowledge_indexer import queue_defect_keys

        queue_defect_keys(project_id, [key])

    # ---------------------------------------------------------------- helpers

    def _browse_url(self, key: str) -> str:
        base = getattr(getattr(self.jira_service, "config", None), "base_url", "") or ""
        return f"{base.rstrip('/')}/browse/{key}" if base else key

    def _require(self, project_id: UUID, report_id: UUID) -> BugReport:
        report = self.get(project_id, report_id)
        if report is None:
            raise LookupError("Bug report not found")
        return report

    @staticmethod
    def _safe(call):
        try:
            return call()
        except Exception as exc:
            logger.warning("Bug report side step failed: %s", exc)
            return None
