"""
Prices LLM token usage in USD.

Resolution order for one model:
1. An override (Token Utilization settings or token_usage.pricing_overrides
   in config), keyed by the litellm model string ("gemini/gemini-2.5-flash"),
   the bare model ("gemini-2.5-flash") or "provider/model".
2. litellm's built-in price map, looked up through llm.lookup_litellm_price
   so llm.py stays the only module that imports litellm.
3. Unpriced: cost 0 and priced=False, so the UI can say "unpriced" instead of
   showing a misleading $0.
"""

from dataclasses import dataclass
from typing import Literal, Mapping

PriceSource = Literal["override", "litellm", "none"]


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float
    output_per_1m: float
    source: PriceSource

    @property
    def priced(self) -> bool:
        return self.source != "none"


@dataclass(frozen=True)
class PricedUsage:
    input_cost_usd: float
    output_cost_usd: float
    cost_usd: float
    priced: bool
    source: PriceSource


UNPRICED = ModelPrice(0.0, 0.0, "none")


def _override_keys(provider: str, model: str, model_string: str) -> list[str]:
    keys = [model_string, model]
    if provider:
        keys.append(f"{provider}/{model}")
    return [k for k in keys if k]


def resolve_price(
    *,
    provider: str,
    model: str,
    model_string: str,
    overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> ModelPrice:
    for key in _override_keys(provider, model, model_string):
        override = (overrides or {}).get(key)
        if override:
            return ModelPrice(
                float(override.get("input_per_1m", 0.0) or 0.0),
                float(override.get("output_per_1m", 0.0) or 0.0),
                "override",
            )

    from backend.services.llm import lookup_litellm_price

    candidates = [model_string, model]
    if provider == "nvidia":
        candidates.insert(0, f"nvidia_nim/{model}")
    for candidate in candidates:
        found = lookup_litellm_price(candidate) if candidate else None
        if found is not None:
            per_token_in, per_token_out = found
            return ModelPrice(per_token_in * 1_000_000, per_token_out * 1_000_000, "litellm")
    return UNPRICED


def price_usage(price: ModelPrice, prompt_tokens: int, completion_tokens: int) -> PricedUsage:
    input_cost = prompt_tokens * price.input_per_1m / 1_000_000
    output_cost = completion_tokens * price.output_per_1m / 1_000_000
    return PricedUsage(
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        cost_usd=input_cost + output_cost,
        priced=price.priced,
        source=price.source,
    )
