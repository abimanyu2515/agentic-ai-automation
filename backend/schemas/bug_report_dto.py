"""Request bodies for the bug report routes. Responses use the BugReport model."""

from pydantic import BaseModel, Field


class BugReportUpdate(BaseModel):
    """Fields a reviewer may edit on a draft. Omitted fields keep their value."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=10000)
    steps_to_reproduce: list[str] | None = Field(default=None, max_length=100)
    expected_result: str | None = Field(default=None, max_length=10000)
    actual_result: str | None = Field(default=None, max_length=10000)
    severity: str | None = None
    priority: str | None = None


class BugReportDismiss(BaseModel):
    reason: str = Field(default="", max_length=500)
