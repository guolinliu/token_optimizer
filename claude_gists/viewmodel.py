"""Presentation data for prompt gist views.

This module is intentionally independent of Textual widgets. It converts
domain records into display-ready rows and detail payloads that the TUI and CLI
can inspect or render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .history import summarize
from .models import PromptGist, TokenUsage, format_tokens, to_local


RowKind = Literal["gist", "header"]


@dataclass(frozen=True)
class ProjectGroup:
    """A project plus the prompts under it, for grouped view."""

    project: str
    gists: list[PromptGist]
    usage: TokenUsage = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", summarize(self.gists))

    @property
    def count(self) -> int:
        return len(self.gists)


@dataclass(frozen=True)
class TableRowView:
    """One display-ready row for the prompt table."""

    kind: RowKind
    project: str
    time: str
    project_label: str
    tokens: str
    input_tokens: str
    output_tokens: str
    cache_write_tokens: str
    cache_read_tokens: str
    model: str
    gist: str
    entry: PromptGist | ProjectGroup
    is_header: bool = False


@dataclass(frozen=True)
class PromptDetailView:
    kind: Literal["gist"]
    when: str
    project: str
    model: str
    session_id: str
    usage_line: str
    text: str


@dataclass(frozen=True)
class GroupDetailView:
    kind: Literal["header"]
    project: str
    count: int
    oldest: str | None
    newest: str | None
    average_tokens: str
    usage_line: str


@dataclass(frozen=True)
class EmptyDetailView:
    kind: Literal["empty"]
    message: str


DetailView = PromptDetailView | GroupDetailView | EmptyDetailView


def group_by_project(gists: list[PromptGist]) -> list[ProjectGroup]:
    """Group gists by project, preserving newest-first project order.

    The project containing the most recent prompt comes first; prompts inside a
    group keep their existing order.
    """
    order: list[str] = []
    buckets: dict[str, list[PromptGist]] = {}
    for g in gists:
        if g.project not in buckets:
            buckets[g.project] = []
            order.append(g.project)
        buckets[g.project].append(g)
    return [ProjectGroup(project, buckets[project]) for project in order]


def short_project(project: str, width: int = 24) -> str:
    return project if len(project) <= width else "…" + project[-(width - 1) :]


class GistsViewModel:
    """Build display-ready state from prompt gists and view options."""

    def __init__(
        self,
        gists: list[PromptGist],
        *,
        grouped: bool = False,
        collapsed: set[str] | None = None,
    ) -> None:
        self.gists = gists
        self.grouped = grouped
        self.collapsed = collapsed or set()

    @property
    def subtitle(self) -> str:
        mode = "grouped" if self.grouped else "flat"
        total = summarize(self.gists)
        return (
            f"{len(self.gists)} prompts · {format_tokens(total.total)} tokens "
            f"· {mode}"
        )

    @property
    def projects(self) -> set[str]:
        return {g.project for g in self.gists}

    def table_rows(self) -> list[TableRowView]:
        if self.grouped:
            return self._grouped_rows()
        return [self._gist_row(g, show_project=True, indent=False) for g in self.gists]

    def project_at_row(self, row_index: int) -> str | None:
        rows = self.table_rows()
        if row_index < 0 or row_index >= len(rows):
            return None
        return rows[row_index].project

    def detail_for_row(self, row_index: int) -> DetailView:
        rows = self.table_rows()
        if row_index < 0 or row_index >= len(rows):
            return EmptyDetailView(
                kind="empty",
                message="No prompts found in local history.",
            )

        row = rows[row_index]
        if row.kind == "header":
            return self._group_detail(row.entry)  # type: ignore[arg-type]
        return self._prompt_detail(row.entry)  # type: ignore[arg-type]

    def _grouped_rows(self) -> list[TableRowView]:
        rows: list[TableRowView] = []
        for group in group_by_project(self.gists):
            collapsed = group.project in self.collapsed
            marker = "▶" if collapsed else "▼"
            count = f"{group.count} prompts" + (" (folded)" if collapsed else "")
            rows.append(
                TableRowView(
                    kind="header",
                    project=group.project,
                    time="",
                    project_label=f"{marker} {short_project(group.project)}",
                    tokens=format_tokens(group.usage.total),
                    input_tokens=format_tokens(group.usage.input_tokens),
                    output_tokens=format_tokens(group.usage.output_tokens),
                    cache_write_tokens=format_tokens(
                        group.usage.cache_creation_input_tokens
                    ),
                    cache_read_tokens=format_tokens(
                        group.usage.cache_read_input_tokens
                    ),
                    model="",
                    gist=count,
                    entry=group,
                    is_header=True,
                )
            )
            if collapsed:
                continue
            rows.extend(
                self._gist_row(g, show_project=False, indent=True) for g in group.gists
            )
        return rows

    @staticmethod
    def _gist_row(
        g: PromptGist, *, show_project: bool, indent: bool
    ) -> TableRowView:
        gist = g.gist_preview(68)
        return TableRowView(
            kind="gist",
            project=g.project,
            time=to_local(g.timestamp).strftime("%m-%d %H:%M"),
            project_label=short_project(g.project) if show_project else "",
            tokens=format_tokens(g.usage.total),
            input_tokens=format_tokens(g.usage.input_tokens),
            output_tokens=format_tokens(g.usage.output_tokens),
            cache_write_tokens=format_tokens(g.usage.cache_creation_input_tokens),
            cache_read_tokens=format_tokens(g.usage.cache_read_input_tokens),
            model=g.model.replace("claude-", "") or "—",
            gist=("  " + gist) if indent else gist,
            entry=g,
        )

    @staticmethod
    def _prompt_detail(g: PromptGist) -> PromptDetailView:
        return PromptDetailView(
            kind="gist",
            when=to_local(g.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            project=g.project,
            model=g.model or "—",
            session_id=g.session_id[:8],
            usage_line=usage_line(g.usage),
            text=g.text,
        )

    @staticmethod
    def _group_detail(group: ProjectGroup) -> GroupDetailView:
        newest = None
        oldest = None
        if group.gists:
            newest = to_local(group.gists[0].timestamp).strftime("%Y-%m-%d %H:%M")
            oldest = to_local(group.gists[-1].timestamp).strftime("%Y-%m-%d %H:%M")
        avg = group.usage.total // group.count if group.count else 0
        return GroupDetailView(
            kind="header",
            project=group.project,
            count=group.count,
            oldest=oldest,
            newest=newest,
            average_tokens=format_tokens(avg),
            usage_line=usage_line(group.usage),
        )


def usage_line(usage: TokenUsage) -> str:
    return (
        f"  total={format_tokens(usage.total)}  "
        f"in={format_tokens(usage.input_tokens)}  "
        f"out={format_tokens(usage.output_tokens)}  "
        f"cache_w={format_tokens(usage.cache_creation_input_tokens)}  "
        f"cache_r={format_tokens(usage.cache_read_input_tokens)}"
    )
