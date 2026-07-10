"""Data models for prompt gists."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class TokenUsage:
    """Token counts accumulated across the assistant turns answering one prompt."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_creation_5m_input_tokens: int = 0
    cache_creation_1h_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total(self) -> int:
        """Total tokens billed/processed for this prompt's response turns."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def add(self, usage: dict) -> None:
        """Accumulate one assistant message's ``usage`` block."""
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)
        self.cache_creation_input_tokens += int(
            usage.get("cache_creation_input_tokens", 0) or 0
        )
        self.cache_read_input_tokens += int(
            usage.get("cache_read_input_tokens", 0) or 0
        )
        # Parse cache creation breakdown if available
        cache_creation = usage.get("cache_creation", {})
        if isinstance(cache_creation, dict):
            self.cache_creation_5m_input_tokens += int(
                cache_creation.get("ephemeral_5m_input_tokens", 0) or 0
            )
            self.cache_creation_1h_input_tokens += int(
                cache_creation.get("ephemeral_1h_input_tokens", 0) or 0
            )


@dataclass
class AssociatedEvent:
    """A single event associated with a prompt gist."""

    event_type: str = ""
    role: str = ""
    message_id: str = ""
    content: str = ""
    timestamp: datetime | None = None
    usage: TokenUsage | None = None
    model: str = ""


@dataclass
class PromptGist:
    """One user prompt plus the token cost of the response it triggered."""

    timestamp: datetime
    project: str
    session_id: str
    text: str
    uuid: str = ""
    model: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    event_type: str = ""
    role: str = ""
    message_id: str = ""
    events: list[AssociatedEvent] = field(default_factory=list)

    @property
    def gist(self) -> str:
        """First non-empty line of the prompt, whitespace-collapsed."""
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped:
                return " ".join(stripped.split())
        return ""

    def gist_preview(self, width: int = 80) -> str:
        g = self.gist
        return g if len(g) <= width else g[: width - 1] + "…"


def format_tokens(n: int) -> str:
    """Human-friendly token count, e.g. 21685 -> '21.7k'."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.2f}M"


def to_local(dt: datetime) -> datetime:
    """Convert an aware/naive UTC datetime to local time for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()
