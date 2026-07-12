import json
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


def _content_by_type(gist, event_type):
    return [e.content for e in gist.events if e.event_type == event_type]


def test_tool_result_events_named_by_tool_and_error(tmp_path):
    """A user tool_result renders as ``(tool_result) <tool> [✗] [detail]`` using
    the tool_use it answers, an error flag, and the file/command it touched."""
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "uuid": "u1",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:00.000Z",
                "message": {"role": "user", "content": "go"},
            }
        ),
        # Assistant emits two tool_use blocks: an Edit and a Bash.
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:01.000Z",
                "message": {
                    "role": "assistant",
                    "id": "m1",
                    "model": "claude-opus-4-8",
                    "content": [
                        {"type": "tool_use", "id": "tu_edit", "name": "Edit"},
                        {"type": "tool_use", "id": "tu_bash", "name": "Bash"},
                    ],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            }
        ),
        # Successful Edit result, with file detail from toolUseResult.
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "uuid": "u2",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:02.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_edit",
                            "content": "ok",
                        }
                    ],
                },
                "toolUseResult": {"type": "update", "filePath": "/repo/src/app.py"},
            }
        ),
        # Failed Bash result.
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "uuid": "u3",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:03.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_bash",
                            "is_error": True,
                            "content": "boom",
                        }
                    ],
                },
            }
        ),
    ]
    (project / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    gists = load_gists(tmp_path)
    assert len(gists) == 1
    tool_results = _content_by_type(gists[0], "user")
    # The typed prompt itself is a "user" event too; keep only tool_result rows.
    tool_results = [c for c in tool_results if c.startswith("(tool_result)")]
    # Tool results now display just the tool name with success/fail icon,
    # without repeating the description or output details.
    assert "(tool_result) Edit ✓" in tool_results
    assert "(tool_result) Bash ✗" in tool_results


def test_attachment_events_show_type_and_brief_detail(tmp_path):
    """Attachments render as ``(subtype) <salient field>`` — the "attachment"
    label is already shown separately, so only subtype + detail belong here."""
    project = tmp_path / "proj"
    project.mkdir()
    lines = [
        json.dumps(
            {
                "type": "user",
                "isSidechain": False,
                "uuid": "u1",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:00.000Z",
                "message": {"role": "user", "content": "go"},
            }
        ),
        json.dumps(
            {
                "type": "attachment",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:01.000Z",
                "attachment": {
                    "type": "hook_success",
                    "hookEvent": "PreToolUse",
                    "hookName": "PreToolUse:Bash",
                    "exitCode": 0,
                    "durationMs": 83,
                },
            }
        ),
        json.dumps(
            {
                "type": "attachment",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:02.000Z",
                "attachment": {"type": "skill_listing", "skillCount": 14},
            }
        ),
        json.dumps(
            {
                "type": "attachment",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:03.000Z",
                "attachment": {
                    "type": "edited_text_file",
                    "filename": "notes.md",
                },
            }
        ),
        json.dumps(
            {
                "type": "attachment",
                "sessionId": "s",
                "timestamp": "2026-06-26T12:00:04.000Z",
                "attachment": {"type": "some_future_type"},
            }
        ),
    ]
    (project / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    gists = load_gists(tmp_path)
    assert len(gists) == 1
    attachments = _content_by_type(gists[0], "attachment")
    assert "(hook_success) PreToolUse:Bash 83ms ✓" in attachments
    assert "(skill_listing) 14 skills" in attachments
    assert "(edited_text_file) notes.md" in attachments
    # Unknown subtypes degrade to just the type label.
    assert "(some_future_type)" in attachments
