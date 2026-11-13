"""Request and response models for the Token Utilization routes."""

from pydantic import BaseModel, Field


class ModelPriceOverride(BaseModel):
    """USD per 1M tokens."""

    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)


class TokenUsageSettings(BaseModel):
    monthly_budget_usd: float = Field(default=0.0, ge=0)  # 0 = no budget
    alert_threshold_pct: float = Field(default=80.0, ge=0, le=100)
    # Keyed by bare model name or litellm model string.
    pricing_overrides: dict[str, ModelPriceOverride] = {}


class ModelPriceRow(BaseModel):
    provider: str
    model: str
    model_string: str
    roles: list[str]
    input_per_1m: float
    output_per_1m: float
    source: str


class TokenUsageCallsPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[dict]
