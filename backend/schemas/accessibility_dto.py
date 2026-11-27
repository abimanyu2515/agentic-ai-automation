"""Accessibility route responses (spec 0011)."""

from pydantic import BaseModel, Field


class AccessibilityGateRun(BaseModel):
    execution_id: str
    gate_status: str


class AccessibilityGateResponse(BaseModel):
    # PASS, WARN, BLOCKED, or NOT_SCANNED
    gate_status: str
    # disabled, scan_error, no_report, batch_running, or batch_no_runs (spec 0012);
    # None when the run was scanned
    gate_reason: str | None = None
    # The mode used (off, warn, or block); None when there is no report,
    # "mixed" when a batch's runs started with different modes.
    gate_mode: str | None = None
    runs: list[AccessibilityGateRun] = Field(default_factory=list)
    # Spec 0012, AC-7: true while this backend is still running the batch.
    # Always false for an execution_id gate and for workspace batches.
    in_progress: bool = False


class AccessibilityScanRequest(BaseModel):
    """Start a standalone scan (AC-10). An empty `paths` means the target URL only."""
    paths: list[str]
    include_login: bool = False


class AccessibilityScanStartResponse(BaseModel):
    report_id: str
    status: str


class AccessibilityScanPage(BaseModel):
    url: str
    # target_url, object_repository, or test_case
    source: str
    # wildcard, for an object repository pattern that cannot be opened
    skipped_reason: str | None = None


class AccessibilityScanPagesResponse(BaseModel):
    pages: list[AccessibilityScanPage]
    max_pages: int
