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


def _first_line(text: str, width: int = 60) -> str:
    """First non-empty line of ``text``, whitespace-collapsed and truncated."""
    if not isinstance(text, str):
        return ""
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            return stripped if len(stripped) <= width else stripped[: width - 1] + "…"
    return ""


def _hook_detail(a: dict) -> str:
    """Salient one-liner for hook-style attachments: ``event:name exit N Nms``."""
    parts: list[str] = []
    event_name = a.get("hookEvent")
    hook_name = a.get("hookName")
    if event_name and hook_name:
        # hookName often already includes the event (e.g. "PreToolUse:Bash"),
        # so avoid a redundant "PreToolUse:PreToolUse:Bash".
        if str(hook_name).startswith(str(event_name)):
            parts.append(str(hook_name))
        else:
            parts.append(f"{event_name}:{hook_name}")
    elif event_name or hook_name:
        parts.append(str(event_name or hook_name))
    code = a.get("exitCode")
    if isinstance(code, int) and code != 0:
        parts.append(f"exit {code}")
    duration = a.get("durationMs")
    if isinstance(duration, (int, float)):
        parts.append(f"{int(duration)}ms")
    return " ".join(parts)


def _attachment_detail(atype: str, a: dict) -> str:
    """Pick the 1-2 most useful fields for an attachment subtype.

    Returns a brief, human-readable string (no surrounding parens); the caller
    prefixes it with ``(<atype>)``. Unknown subtypes return "" so display falls
    back to just the type label.
    """
    hook_types = {
        "hook_success",
        "hook_system_message",
        "hook_additional_context",
        "async_hook_response",
        "hook_non_blocking_error",
        "hook_cancelled",
    }
    if atype in hook_types:
        detail = _hook_detail(a)
        if atype == "hook_non_blocking_error":
            detail = f"{detail} error".strip()
        elif atype == "hook_cancelled" and a.get("timedOut"):
            detail = f"{detail} timeout".strip()
        return detail
    if atype == "task_reminder":
        n = a.get("itemCount")
        return f"{n} items" if n is not None else ""
    if atype == "skill_listing":
        n = a.get("skillCount")
        return f"{n} skills" if n is not None else ""
    if atype == "command_permissions":
        tools = a.get("allowedTools")
        return f"{len(tools)} tools" if isinstance(tools, list) else ""
    if atype == "queued_command":
        return _first_line(a.get("prompt", ""))
    if atype in ("edited_text_file", "file"):
        return a.get("displayPath") or a.get("filename") or ""
    if atype == "directory":
        return a.get("displayPath") or a.get("path") or ""
    if atype == "date_change":
        return str(a.get("newDate", "") or "")
    if atype in ("plan_mode", "plan_mode_exit", "plan_mode_reentry"):
        path = a.get("planFilePath")
        return os.path.basename(path) if isinstance(path, str) and path else ""
    return ""


def _summarize_attachment(attachment: dict) -> str:
    """Render an attachment as ``(<subtype>) <brief detail>``.

    The event type ("attachment") is already shown by the caller, so we only
    emit the subtype and its salient fields here.
    """
    atype = attachment.get("type", "") or "attachment"
    detail = _attachment_detail(atype, attachment)
    return f"({atype}) {detail}".rstrip()


def _tool_result_detail(tool_use_result) -> str:
    """A short detail for a tool_result from the event's ``toolUseResult``.

    Prefers the file touched (Read/Edit/Write) or the skill/command name, so a
    result reads e.g. ``Read app.py`` or ``Skill calendar`` instead of a bare
    tool name.
    """
    if not isinstance(tool_use_result, dict):
        return ""
    file_path = tool_use_result.get("filePath")
    if not isinstance(file_path, str) or not file_path:
        nested = tool_use_result.get("file")
        if isinstance(nested, dict):
            file_path = nested.get("filePath")
    if isinstance(file_path, str) and file_path:
        return os.path.basename(file_path)
    command = tool_use_result.get("commandName")
    if isinstance(command, str) and command:
        return command
    return ""


def _record_tool_names(event: dict, tool_name_map: dict[str, str]) -> None:
    """Record ``tool_use`` id -> tool name from an assistant event.

    Populated as we scan the transcript so a later user ``tool_result`` (which
    only carries ``tool_use_id``) can be labeled with the tool that produced it.
    """
    message = event.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for blk in content:
        if not isinstance(blk, dict) or blk.get("type") != "tool_use":
            continue
        tid = blk.get("id")
        name = blk.get("name")
        if tid and name:
            tool_name_map[tid] = name


def _extract_event_content(
    event: dict,
    message: dict,
    tool_name_map: dict[str, str] | None = None,
) -> str:
    """Return a text preview of an event's content."""
    etype = event.get("type", "")

    # Attachments carry their useful detail outside message.content; the event
    # type ("attachment") is already shown by the caller, so emit "(type) detail".
    if etype == "attachment":
        attachment = event.get("attachment", {})
        if isinstance(attachment, dict):
            return _summarize_attachment(attachment)
        return "(attachment)"

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
            elif btype == "tool_result":
                # Resolve which tool produced this result and flag failures, so
                # "(tool_result)" becomes e.g. "(tool_result) Bash ✗ app.py".
                resolved = (tool_name_map or {}).get(blk.get("tool_use_id", ""), "")
                label = resolved or "tool"
                if blk.get("is_error"):
                    label += " ✗"
                detail = _tool_result_detail(event.get("toolUseResult"))
                if detail:
                    label += f" {detail}"
                tool_names.append(label)

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

    if etype:
        if types_str:
            return f"({etype} | {types_str})"
        return f"({etype})"

    return ""


def _make_associated_event(
    event: dict,
    tool_name_map: dict[str, str] | None = None,
) -> AssociatedEvent:
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

    content = _extract_event_content(event, message, tool_name_map)
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
    # tool_use id -> tool name, so user tool_result events can name their tool.
    tool_name_map: dict[str, str] = {}

    def _preamble(event: dict) -> PromptGist:
        gist = PromptGist(
            timestamp=_parse_timestamp(event.get("timestamp")) or datetime.min,
            project=project,
            session_id=event.get("sessionId", session_id),
            text="(session preamble — tokens before first prompt)",
            event_type="assistant",
            role="assistant",
        )
        gist.events.append(_make_associated_event(event, tool_name_map))
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

            # Record any tool_use ids before we render dependent tool_result rows.
            _record_tool_names(event, tool_name_map)

            if etype == "user" and not event.get("isSidechain"):
                text = _extract_user_text(message.get("content"))
                if text is None or _is_command_noise(text):
                    # Not a real prompt (tool result, command noise); keep
                    # accumulating into the previous prompt if any.
                    if current is not None:
                        current.events.append(
                            _make_associated_event(event, tool_name_map)
                        )
                    continue
                if current is not None:
                    yield current
                current = PromptGist(
                    timestamp=_parse_timestamp(event.get("timestamp")) or datetime.min,
                    project=project,
                    session_id=event.get("sessionId", session_id),
                    text=text,
                    uuid=event.get("uuid", "") or "",
                    event_type=etype or "",
                    role=message.get("role", "") if isinstance(message, dict) else "",
                    message_id=message.get("id", "")
                    if isinstance(message, dict)
                    else "",
                )
                current.events.append(_make_associated_event(event, tool_name_map))
            elif etype == "assistant":
                if current is not None:
                    current.events.append(_make_associated_event(event, tool_name_map))
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
                    current.events.append(_make_associated_event(event, tool_name_map))

    if current is not None:
        yield current


def _dedupe_forked_gists(
    entries: list[tuple[PromptGist, float]],
) -> list[PromptGist]:
    """Drop fork/branch duplicates, keeping the copy from the newest file.

    Claude Code fork/branch operations copy a session's transcript verbatim —
    including each event's ``uuid`` — into a new file and then diverge, so the
    older file's records are a prefix of the newer one's. The same user-prompt
    ``uuid`` therefore appears in multiple files. For each duplicated uuid we
    keep the gist from the file with the newest mtime, which holds the most
    complete set of associated events. Gists without a user-prompt uuid (e.g.
    synthetic session preambles) can't be matched this way and are always kept.

    ``entries`` pairs each gist with its source file's mtime.
    """
    best: dict[str, tuple[PromptGist, float]] = {}
    kept: list[PromptGist] = []
    for gist, mtime in entries:
        uuid = gist.uuid
        if not uuid:
            kept.append(gist)
            continue
        existing = best.get(uuid)
        if existing is None or mtime > existing[1]:
            best[uuid] = (gist, mtime)
    kept.extend(gist for gist, _ in best.values())
    return kept


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
    entries: list[tuple[PromptGist, float]] = []
    for jsonl in projects_dir.glob("*/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        try:
            for gist in iter_session_gists(jsonl):
                if project_filter and project_filter.lower() not in (
                    gist.project.lower()
                ):
                    continue
                entries.append((gist, mtime))
        except OSError:
            continue

    gists = _dedupe_forked_gists(entries)

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
