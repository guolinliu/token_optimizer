"""Discover and parse local Claude Code history into PromptGist records.

Session transcripts live at ``~/.claude/projects/<encoded-cwd>/<session>.jsonl``.
Each line is a JSON event. A typed user prompt is ``{"type": "user", ...}`` whose
``message.content`` is human text. The assistant turns that follow it (until the
next user prompt) each carry a ``message.usage`` block; we sum those into the
prompt's token cost.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .models import PromptGist, TokenUsage


def default_projects_dir() -> Path:
    """Location of Claude Code project transcripts."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    return Path(base) / "projects"


def decode_project_name(dir_name: str) -> str:
    """Turn an encoded transcript dir name into a readable project label.

    Claude encodes the cwd by replacing path separators with ``-`` and prefixing
    a leading ``-``. We can't perfectly recover the original path (real ``-`` and
    path separators are indistinguishable), so we just strip the leading dash and
    keep the rest; the table truncates it for display.
    """
    return dir_name.lstrip("-") or dir_name


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # Stored as e.g. "2026-06-26T12:51:48.697Z"
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_user_text(content) -> str | None:
    """Return human-typed text from a user message ``content``, or None.

    ``content`` is usually a string. It can also be a list of blocks; we keep the
    text blocks and skip tool_result / image blocks so tool-output user messages
    don't masquerade as prompts.
    """
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if isinstance(content, list):
        texts = [
            blk.get("text", "")
            for blk in content
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        joined = "\n".join(t for t in texts if t).strip()
        return joined or None
    return None


def _is_command_noise(text: str) -> bool:
    """Skip slash-command stdout / meta envelopes that aren't real prompts."""
    stripped = text.lstrip()
    return stripped.startswith("<command-") or stripped.startswith(
        "<local-command-"
    ) or stripped.startswith("Caveat:")


def iter_session_gists(path: Path) -> Iterator[PromptGist]:
    """Yield a PromptGist per typed prompt in one session transcript."""
    project = decode_project_name(path.parent.name)
    current: PromptGist | None = None

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            message = event.get("message") or {}

            if etype == "user" and not event.get("isSidechain"):
                text = _extract_user_text(message.get("content"))
                if text is None or _is_command_noise(text):
                    # Not a real prompt (tool result, command noise); keep
                    # accumulating into the previous prompt if any.
                    continue
                if current is not None:
                    yield current
                current = PromptGist(
                    timestamp=_parse_timestamp(event.get("timestamp"))
                    or datetime.min,
                    project=project,
                    session_id=event.get("sessionId", path.stem),
                    text=text,
                )
            elif etype == "assistant" and current is not None:
                usage = message.get("usage")
                if isinstance(usage, dict):
                    current.usage.add(usage)
                model = message.get("model")
                if model and not current.model:
                    current.model = model

    if current is not None:
        yield current


def load_gists(
    projects_dir: Path | None = None,
    *,
    project_filter: str | None = None,
    limit: int | None = None,
) -> list[PromptGist]:
    """Load prompt gists across all sessions, newest first.

    ``project_filter`` is a case-insensitive substring matched against the
    decoded project label. ``limit`` caps the number of returned gists.
    """
    projects_dir = projects_dir or default_projects_dir()
    if not projects_dir.exists():
        return []

    gists: list[PromptGist] = []
    for jsonl in projects_dir.glob("*/*.jsonl"):
        try:
            for gist in iter_session_gists(jsonl):
                if project_filter and project_filter.lower() not in (
                    gist.project.lower()
                ):
                    continue
                gists.append(gist)
        except OSError:
            continue

    gists.sort(key=lambda g: g.timestamp, reverse=True)
    if limit is not None:
        gists = gists[:limit]
    return gists


def summarize(gists: Iterable[PromptGist]) -> TokenUsage:
    """Sum token usage across a collection of gists."""
    total = TokenUsage()
    for g in gists:
        total.input_tokens += g.usage.input_tokens
        total.output_tokens += g.usage.output_tokens
        total.cache_creation_input_tokens += g.usage.cache_creation_input_tokens
        total.cache_read_input_tokens += g.usage.cache_read_input_tokens
    return total
