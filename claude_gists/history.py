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
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .models import AssociatedEvent, PromptGist, TokenUsage


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
    return (
        stripped.startswith("<command-")
        or stripped.startswith("<local-command-")
        or stripped.startswith("Caveat:")
    )


def _extract_event_content(event: dict, message: dict) -> str:
    """Return a text preview of an event's content."""
    etype = event.get("type", "")

    # Try to get text from message.content
    content = message.get("content")
    text = _extract_user_text(content)

    # Collect content types from blocks and tool names
    # For tool_use blocks, the tool name should be displayed outside the brackets,
    # just like text content is displayed outside the (text) type.
    content_types = []
    tool_names = []
    if isinstance(content, list):
        for blk in content:
            if not isinstance(blk, dict):
                continue
            btype = blk.get("type", "")
            if not btype:
                continue
            content_types.append(btype)
            if btype == "tool_use":
                name = blk.get("name", "")
                if name == "Skill":
                    skill = blk.get("input", {}).get("skill", "")
                    if skill:
                        tool_names.append(f"Skill:{skill}")
                    else:
                        tool_names.append("Skill")
                elif name:
                    tool_names.append(name)

    types_str = f"({', '.join(content_types)})" if content_types else ""

    # Build the content string: text followed by tool names
    content_parts = []
    if text:
        # Truncate long text
        if len(text) > 200:
            text = text[:197] + "..."
        content_parts.append(text)
    if tool_names:
        content_parts.extend(tool_names)

    content_str = " ".join(content_parts)

    if content_str:
        if types_str:
            return f"{types_str} {content_str}"
        return content_str

    # For assistant events with no text content, show types
    if etype == "assistant":
        if types_str:
            return types_str
        return "(assistant)"

    # For user events with tool results (content is list without text blocks)
    if etype == "user" and isinstance(content, list):
        if types_str:
            return types_str
        return "(tool result)"

    # For other events, show type and maybe a summary
    if etype == "attachment":
        attachment = event.get("attachment", {})
        if isinstance(attachment, dict):
            atype = attachment.get("type", "")
            if atype:
                return f"(attachment: {atype})"
        return "(attachment)"

    if etype:
        if types_str:
            return f"({etype} | {types_str})"
        return f"({etype})"

    return ""


def _make_associated_event(event: dict) -> AssociatedEvent:
    """Create an AssociatedEvent from a raw event dict."""
    etype = event.get("type", "")
    message = event.get("message") or {}
    role = message.get("role", "") if isinstance(message, dict) else ""
    message_id = ""
    model = ""
    if isinstance(message, dict):
        message_id = message.get("id", "") or event.get("uuid", "")
        model = message.get("model", "") or ""
    else:
        message_id = event.get("uuid", "")

    content = _extract_event_content(event, message)
    timestamp = _parse_timestamp(event.get("timestamp"))

    usage = None
    if isinstance(message, dict):
        usage_dict = message.get("usage")
        if isinstance(usage_dict, dict):
            u = TokenUsage()
            u.add(usage_dict)
            # Only store if there's actual usage
            if u.total > 0:
                usage = u

    return AssociatedEvent(
        event_type=etype,
        role=role,
        message_id=message_id,
        content=content,
        timestamp=timestamp,
        usage=usage,
        model=model,
    )


def iter_session_gists(path: Path) -> Iterator[PromptGist]:
    """Yield a PromptGist per typed prompt in one session transcript.

    Token usage lives only on ``assistant`` events, but a single assistant
    message (one ``message.id``) is written across multiple JSONL lines
    (thinking / text / tool_use), each repeating the *same* ``message.usage``.
    We therefore accumulate usage at most once per ``message.id`` to avoid the
    ~3x over-count that summing every line would produce. Assistant usage that
    appears before any typed prompt (e.g. resumed/compacted sessions) is
    attributed to a synthetic "session preamble" record so it is not dropped.
    """
    project = decode_project_name(path.parent.name)
    session_id = path.stem
    current: PromptGist | None = None
    # message.id values whose usage we've already counted (dedupe split blocks).
    counted_ids: set[str] = set()

    def _preamble(event: dict) -> PromptGist:
        gist = PromptGist(
            timestamp=_parse_timestamp(event.get("timestamp")) or datetime.min,
            project=project,
            session_id=event.get("sessionId", session_id),
            text="(session preamble — tokens before first prompt)",
            event_type="assistant",
            role="assistant",
        )
        gist.events.append(_make_associated_event(event))
        return gist

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
                    if current is not None:
                        current.events.append(_make_associated_event(event))
                    continue
                if current is not None:
                    yield current
                current = PromptGist(
                    timestamp=_parse_timestamp(event.get("timestamp")) or datetime.min,
                    project=project,
                    session_id=event.get("sessionId", session_id),
                    text=text,
                    event_type=etype or "",
                    role=message.get("role", "") if isinstance(message, dict) else "",
                    message_id=message.get("id", "")
                    if isinstance(message, dict)
                    else "",
                )
                current.events.append(_make_associated_event(event))
            elif etype == "assistant":
                if current is not None:
                    current.events.append(_make_associated_event(event))
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                msg_id = message.get("id") or ""
                # Count each assistant message's usage exactly once, even when
                # its content blocks are split across several event lines.
                if msg_id and msg_id in counted_ids:
                    continue
                if msg_id:
                    counted_ids.add(msg_id)
                if current is None:
                    current = _preamble(event)
                else:
                    # Event already added above, but preamble creates a new gist
                    # with the event already added
                    pass
                current.usage.add(usage)
                model = message.get("model")
                if model and not current.model:
                    current.model = model
            else:
                # Other events (system, attachment, etc.) - associate with current gist
                # Skip sidechain user events entirely
                if etype == "user" and event.get("isSidechain"):
                    continue
                if current is not None:
                    current.events.append(_make_associated_event(event))

    if current is not None:
        yield current


def load_gists(
    projects_dir: Path | None = None,
    *,
    project_filter: str | None = None,
    limit: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[PromptGist]:
    """Load prompt gists across all sessions, newest first.

    ``project_filter`` is a case-insensitive substring matched against the
    decoded project label. ``limit`` caps the number of returned gists.
    ``since`` and ``until`` filter by timestamp (inclusive).
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

    def _ensure_aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    if since is not None:
        since_aware = _ensure_aware(since)
        gists = [g for g in gists if _ensure_aware(g.timestamp) >= since_aware]
    if until is not None:
        until_aware = _ensure_aware(until)
        gists = [g for g in gists if _ensure_aware(g.timestamp) <= until_aware]
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
