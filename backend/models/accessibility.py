"""
Accessibility reports (spec 0011).

One report holds the axe-core results of one test run (`source=execution`)
or of one standalone scan (`source=scan`): the pages scanned, the findings
merged by rule and element, the counts by impact, and the release gate.

The gate never changes the run's functional `ExecutionResult.status`.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AccessibilitySource(str, Enum):
    EXECUTION = "execution"
    SCAN = "scan"


class AccessibilityReportStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GateMode(str, Enum):
    OFF = "off"
    WARN = "warn"
    BLOCK = "block"


class GateStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCKED = "BLOCKED"
    NOT_SCANNED = "NOT_SCANNED"


class FindingKind(str, Enum):
    VIOLATION = "violation"
    NEEDS_REVIEW = "needs_review"


# Highest first. `needs_review` findings sort after every impact.
IMPACT_ORDER = ("critical", "serious", "moderate", "minor")

# Project settings (AC-13): `Literal` typed, so any other value is a 422.
A11yGateModeSetting = Literal["off", "warn", "block"]
A11yBlockSeveritySetting = Literal["critical", "serious"]

GATE_REASON_DISABLED = "disabled"
GATE_REASON_SCAN_ERROR = "scan_error"


class AccessibilityPage(BaseModel):
    url: str
    title: str = ""
    scanned_at: datetime | None = None
    requires_login: bool = False
    # budget, csp_blocked, requires_login, navigation_failed, axe_error, or None
    error: str | None = None


class AccessibilityFinding(BaseModel):
    # sha256 of "a11y:" + rule id + normalized selector
    signature: str
    kind: FindingKind
    rule_id: str
    impact: str | None = None
    wcag_criteria: list[str] = Field(default_factory=list)
    help: str = ""
    help_url: str = ""
    selector: str = ""
    # Redacted, at most 500 characters
    html_snippet: str = ""
    pages: list[str] = Field(default_factory=list)
    occurrences: int = 0
    # Redacted
    failure_summary: str = ""
    ai_explanation: str = ""
    ai_fix: str = ""
    # llm, or axe_fallback
    ai_source: str = "axe_fallback"
    # A file name relative to the run folder, served only by the screenshot route
    screenshot_path: str | None = None
    bug_report_id: UUID | None = None


class AccessibilityGuidance(BaseModel):
    """The LLM's guidance for one finding, matched back by `id` (AC-4)."""
    id: int
    explanation: str
    fix: str = ""
    # needs_review findings only: what a person must check by hand
    review_guidance: str = ""


class AccessibilityEnrichment(BaseModel):
    """The structured response of the one enrichment call per report (AC-4)."""
    items: list[AccessibilityGuidance] = Field(default_factory=list)


def empty_counts() -> dict[str, int]:
    return {"critical": 0, "serious": 0, "moderate": 0, "minor": 0, "needs_review": 0}


class AccessibilityReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source: AccessibilitySource = AccessibilitySource.EXECUTION
    execution_id: UUID | None = None
    batch_id: str | None = None
    test_case_id: UUID | None = None
    gate_mode: GateMode = GateMode.WARN
    status: AccessibilityReportStatus = AccessibilityReportStatus.SUCCEEDED
    # scan_error, login_failed, or timeout
    error: str | None = None
    # not_requested, succeeded, or failed
    login_status: str = "not_requested"
    wcag_tags: list[str] = Field(default_factory=list)
    pages: list[AccessibilityPage] = Field(default_factory=list)
    truncated: bool = False
    findings: list[AccessibilityFinding] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=empty_counts)
    gate_status: GateStatus = GateStatus.NOT_SCANNED
    # disabled, or scan_error
    gate_reason: str | None = None
    created_by: UUID | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    finished_at: datetime | None = None
