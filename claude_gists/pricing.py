"""Claude API token cost estimation.

Rates are public Claude API prices in USD per million tokens. Local Claude Code
history records cache creation tokens, but not the selected cache TTL, so cache
writes are estimated with the 5-minute prompt-cache write rate.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import TokenUsage


@dataclass(frozen=True)
class ClaudeRates:
    input: float
    output: float
    cache_write_5min: float
    cache_read: float


RATES_BY_MODEL: dict[str, ClaudeRates] = {
    "claude-opus-4-8": ClaudeRates(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-7": ClaudeRates(5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-6": ClaudeRates(5.00, 25.00, 6.25, 0.50),
    "claude-sonnet-4-6": ClaudeRates(3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-5": ClaudeRates(3.00, 15.00, 3.75, 0.30),
    "claude-haiku-4-5": ClaudeRates(1.00, 5.00, 1.25, 0.10),
    "claude-haiku-4-5-20251001": ClaudeRates(1.00, 5.00, 1.25, 0.10),
    "claude-haiku-3-5": ClaudeRates(0.80, 4.00, 1.00, 0.08),
}


def normalize_model(model: str) -> str:
    """Normalize concrete Claude model IDs to pricing-table keys."""
    if model in RATES_BY_MODEL:
        return model
    # Some API model IDs carry date suffixes. Keep named versions like
    # claude-haiku-4-5-20251001 when explicitly priced above.
    parts = model.split("-")
    if len(parts) > 4 and parts[-1].isdigit() and len(parts[-1]) == 8:
        candidate = "-".join(parts[:-1])
        if candidate in RATES_BY_MODEL:
            return candidate
    return model


def has_pricing(model: str) -> bool:
    return normalize_model(model) in RATES_BY_MODEL


def estimate_cost_usd(
    model: str, usage: TokenUsage, *, fallback_model: str | None = None
) -> float | None:
    rates = RATES_BY_MODEL.get(normalize_model(model))
    if rates is None and fallback_model is not None:
        rates = RATES_BY_MODEL.get(normalize_model(fallback_model))
    if rates is None:
        return None

    return (
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_creation_input_tokens * rates.cache_write_5min
        + usage.cache_read_input_tokens * rates.cache_read
    ) / 1_000_000


def format_cost(cost: float | None) -> str:
    if cost is None:
        return "—"
    if cost == 0:
        return "$0"
    if cost < 0.01:
        return f"${cost:.4f}"
    if cost < 1000:
        return f"${cost:.2f}"
    return f"${cost / 1000:.1f}k"
