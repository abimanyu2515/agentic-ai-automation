"""One billable LLM completion attempt (Token Utilization feature)."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TokenUsageRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_now)

    # Attribution
    agent: str = "unattributed"
    operation: str | None = None
    project_id: UUID | None = None
    requirement_id: UUID | None = None
    test_case_id: UUID | None = None
    run_id: UUID | None = None

    # Model
    role: str | None = None  # "analysis" | "generation" | None for an explicit config
    provider: str = ""
    model: str = ""

    # Tokens
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Cost (USD)
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    cost_usd: float = 0.0
    priced: bool = False
    price_source: Literal["override", "litellm", "none"] = "none"

    # Call
    latency_ms: int = 0
    status: Literal["success", "error", "rate_limited"] = "success"
    error_type: str | None = None
    attempt: int = 1


class TokenUsageFilters(BaseModel):
    """Filters shared by every token usage query."""

    start: datetime | None = None
    end: datetime | None = None
    project_id: UUID | None = None
    agent: str | None = None
    model: str | None = None
    role: str | None = None
    provider: str | None = None
    status: str | None = None
    operation: str | None = None

    def matches(self, record: TokenUsageRecord) -> bool:
        if self.start and record.created_at < self.start:
            return False
        if self.end and record.created_at >= self.end:
            return False
        if self.project_id and record.project_id != self.project_id:
            return False
        for field in ("agent", "model", "role", "provider", "status", "operation"):
            wanted = getattr(self, field)
            if wanted and getattr(record, field) != wanted:
                return False
        return True
