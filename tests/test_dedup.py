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


def _write(path: Path, lines: list[str], mtime: float) -> None:
    import os

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _user(uuid: str, text: str, ts: str) -> str:
    import json

    return json.dumps(
        {
            "type": "user",
            "isSidechain": False,
            "uuid": uuid,
            "sessionId": "s",
            "timestamp": ts,
            "message": {"role": "user", "content": text},
        }
    )


def _assistant(msg_id: str, ts: str, inp: int, out: int) -> str:
    import json

    return json.dumps(
        {
            "type": "assistant",
            "sessionId": "s",
            "timestamp": ts,
            "message": {
                "role": "assistant",
                "id": msg_id,
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }
    )


def test_forked_sessions_deduped_by_user_prompt_uuid(tmp_path):
    """A fork copies a session's prompts (same uuids) into a newer file, then
    diverges. The older file is a prefix; we keep the newer file's copy of each
    duplicated user prompt and its (more complete) token usage."""
    project = tmp_path / "forked-project"
    project.mkdir()

    # Older file: prefix with just the first prompt and a partial usage record.
    old = project / "old.jsonl"
    _write(
        old,
        [
            _user("u1", "prompt one", "2026-06-26T12:00:00.000Z"),
            _assistant("m1", "2026-06-26T12:00:01.000Z", inp=10, out=5),
        ],
        mtime=1000.0,
    )

    # Newer file: same first prompt uuid (more complete usage) plus a 2nd prompt.
    new = project / "new.jsonl"
    _write(
        new,
        [
            _user("u1", "prompt one", "2026-06-26T12:00:00.000Z"),
            _assistant("m1", "2026-06-26T12:00:01.000Z", inp=100, out=50),
            _user("u2", "prompt two", "2026-06-26T12:05:00.000Z"),
            _assistant("m2", "2026-06-26T12:05:01.000Z", inp=20, out=10),
        ],
        mtime=2000.0,
    )

    gists = load_gists(tmp_path)

    # u1 appears in both files but is counted once; u2 only in the newer file.
    assert len(gists) == 2
    by_text = {g.text: g for g in gists}
    assert set(by_text) == {"prompt one", "prompt two"}

    # The surviving "prompt one" is the newer file's copy (input 100, not 10).
    assert by_text["prompt one"].uuid == "u1"
    assert by_text["prompt one"].usage.input_tokens == 100
    assert by_text["prompt one"].usage.output_tokens == 50

    # Total counts each duplicated prompt once: 150 (u1 new) + 30 (u2) = 180.
    assert summarize(gists).total == 180


def test_fork_dedup_keeps_newest_regardless_of_file_order(tmp_path):
    """Selection is by file mtime, not filesystem/glob iteration order: a name
    that sorts first but is older must lose to the newer file's copy."""
    project = tmp_path / "forked-project"
    project.mkdir()

    # "a.jsonl" sorts before "b.jsonl" but is the NEWER (more complete) file.
    newer = project / "a.jsonl"
    _write(
        newer,
        [
            _user("u1", "shared prompt", "2026-06-26T12:00:00.000Z"),
            _assistant("m1", "2026-06-26T12:00:01.000Z", inp=100, out=50),
        ],
        mtime=5000.0,
    )
    older = project / "b.jsonl"
    _write(
        older,
        [
            _user("u1", "shared prompt", "2026-06-26T12:00:00.000Z"),
            _assistant("m1", "2026-06-26T12:00:01.000Z", inp=1, out=1),
        ],
        mtime=1000.0,
    )

    gists = load_gists(tmp_path)
    assert len(gists) == 1
    assert gists[0].usage.input_tokens == 100
