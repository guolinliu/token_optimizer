from pathlib import Path

from claude_gists.history import load_gists, summarize
from claude_gists.models import format_tokens

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_gists_pairs_prompts_with_usage():
    gists = load_gists(FIXTURES)
    # Three real prompts; tool_result and sidechain are skipped.
    assert len(gists) == 3

    # Newest first: bracket prompt, then "Second prompt", then "First prompt".
    bracket, second, first = gists
    assert second.text == "Second prompt"
    assert first.gist == "First prompt: build a thing"

    # First prompt accumulates BOTH following assistant turns.
    assert first.usage.input_tokens == 300
    assert first.usage.output_tokens == 130
    assert first.usage.cache_creation_input_tokens == 10
    assert first.usage.cache_read_input_tokens == 25
    assert first.usage.total == 465
    assert first.model == "claude-opus-4-8"

    # Second prompt only its own turn.
    assert second.usage.total == 400
    assert second.model == "claude-sonnet-4-6"


def test_project_filter():
    assert load_gists(FIXTURES, project_filter="sample")
    assert load_gists(FIXTURES, project_filter="nomatch") == []


def test_limit():
    assert len(load_gists(FIXTURES, limit=1)) == 1


def test_summarize():
    total = summarize(load_gists(FIXTURES))
    assert total.total == 880  # 465 + 400 + 15


def test_missing_dir_returns_empty():
    assert load_gists(Path("/nonexistent/path/xyz")) == []


def test_format_tokens():
    assert format_tokens(500) == "500"
    assert format_tokens(21685) == "21.7k"
    assert format_tokens(2_500_000) == "2.50M"
