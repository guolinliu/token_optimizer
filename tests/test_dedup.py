"""Token accounting must count each assistant message.id once (not per block).

An assistant message is split across multiple JSONL lines (thinking / text /
tool_use), each repeating the same ``message.usage``. Summing every line would
over-count ~3x. Usage before the first prompt must be captured, not dropped.
"""

from pathlib import Path

from claude_gists.history import load_gists, summarize

FIXTURES = Path(__file__).parent / "fixtures_dedup"


def test_usage_deduped_by_message_id_and_preamble_captured():
    gists = load_gists(FIXTURES)
    # One synthetic preamble (m0, before any prompt) + one real prompt.
    assert len(gists) == 2

    preamble, prompt = sorted(gists, key=lambda g: g.timestamp)

    # m0 counted once despite 2 split lines: input=100.
    assert preamble.role == "assistant"
    assert preamble.usage.total == 100

    # m1 counted once despite 3 split lines: input=1000 + output=100 = 1100,
    # NOT 3300.
    assert prompt.text == "do a thing"
    assert prompt.usage.input_tokens == 1000
    assert prompt.usage.output_tokens == 100
    assert prompt.usage.total == 1100

    # Grand total reflects both messages once each.
    assert summarize(gists).total == 1200
