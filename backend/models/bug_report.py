"""
BugReport.

A structured bug report drafted by the Bug Reporting Agent from a triaged
execution failure. It lives in the application as a draft until a person
reviews it and submits it to Jira (or a project opts into auto-submit).

One open report covers one failure signature per test case: when the same
error happens again, the run is appended to `occurrences` instead of
producing a second draft.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BugReportStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    LINKED_DUPLICATE = "linked_duplicate"
    DISMISSED = "dismissed"
    SUBMIT_FAILED = "submit_failed"
    RESOLVED = "resolved"


# Reports in these states still collect new occurrences of the same failure.
OPEN_STATUSES = frozenset({
    BugReportStatus.DRAFT,
    BugReportStatus.SUBMITTED,
    BugReportStatus.LINKED_DUPLICATE,
    BugReportStatus.SUBMIT_FAILED,
})

# Only these states can be edited or submitted.
EDITABLE_STATUSES = frozenset({BugReportStatus.DRAFT, BugReportStatus.SUBMIT_FAILED})

SEVERITIES = ("critical", "major", "minor", "trivial")

# Spec 0011, AC-6: accessibility findings become bugs in their own category.
# Functional lookups (get_by_execution, find_open_by_signature) leave it out,
# so one run can hold a functional bug and accessibility bugs side by side.
ACCESSIBILITY_CATEGORY = "Accessibility Issue"


class BugOccurrence(BaseModel):
    # A functional occurrence names a run; an accessibility occurrence names
    # the report it came from (and its run, when the report has one).
    execution_id: UUID | None = None
    accessibility_report_id: UUID | None = None
    executed_at: datetime


class BugReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    # Spec 0011: an accessibility bug from a standalone scan has no run and no
    # test case. At least one of execution_id or accessibility_report_id is set.
    execution_id: UUID | None = None
    test_case_id: UUID | None = None
    accessibility_report_id: UUID | None = None
    scenario_id: UUID | None = None
    requirement_id: UUID | None = None

    title: str = Field(min_length=1, max_length=255)
    summary: str = ""
    steps_to_reproduce: list[str] = Field(default_factory=list)
    expected_result: str = ""
    actual_result: str = ""
    severity: str = "major"
    priority: str = "medium"
    # Normalized URL, browser version and framework. Never a secret.
    environment: dict[str, str] = Field(default_factory=dict)

    failing_step: str | None = None
    root_cause: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = None
    flaky_score: float | None = None
    failure_category: str | None = None

    # Accessibility bugs only (spec 0011, AC-7): they become the
    # `a11y_<rule>` and `wcag_<criterion>` Jira labels.
    a11y_rule_id: str | None = None
    wcag_criteria: list[str] = Field(default_factory=list)

    attachments: list[str] = Field(default_factory=list)
    error_signature: str = ""
    occurrences: list[BugOccurrence] = Field(default_factory=list)

    status: BugReportStatus = BugReportStatus.DRAFT
    jira_key: str | None = None
    jira_url: str | None = None
    duplicate_of_jira_key: str | None = None
    submit_error: str | None = None
    dismiss_reason: str | None = None

    created_by: str = "agent"
    submitted_by: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def _needs_a_source(self) -> "BugReport":
        if self.execution_id is None and self.accessibility_report_id is None:
            raise ValueError("A bug report needs an execution_id or an accessibility_report_id")
        return self

    @property
    def is_accessibility(self) -> bool:
        return self.failure_category == ACCESSIBILITY_CATEGORY

    def touch(self) -> None:
        self.updated_at = _utc_now()
