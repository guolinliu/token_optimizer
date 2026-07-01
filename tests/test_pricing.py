from claude_gists.models import TokenUsage
from claude_gists.pricing import estimate_cost_usd, format_cost, normalize_model


def test_estimate_cost_uses_public_claude_rates():
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )

    assert estimate_cost_usd("claude-opus-4-8", usage) == 36.75
    assert estimate_cost_usd("claude-sonnet-4-6", usage) == 22.05


def test_pricing_helpers_handle_model_suffixes_and_unknown_models():
    assert normalize_model("claude-sonnet-4-6-20260630") == "claude-sonnet-4-6"
    assert estimate_cost_usd("not-a-claude-model", TokenUsage()) is None
    assert (
        estimate_cost_usd(
            "not-a-claude-model",
            TokenUsage(input_tokens=1_000_000),
            fallback_model="claude-sonnet-4-6",
        )
        == 3.00
    )
    assert format_cost(None) == "—"
    assert format_cost(0.004825) == "$0.0048"
