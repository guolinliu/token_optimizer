"""Presentation data for prompt gist views.

This module is intentionally independent of Textual widgets. It converts
domain records into display-ready rows and detail payloads that the TUI and CLI
can inspect or render.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from .history import summarize
from .models import AssociatedEvent, PromptGist, TokenUsage, format_tokens, to_local
from .pricing import estimate_cost_usd, format_cost, has_pricing


RowKind = Literal["gist", "header"]


@dataclass(frozen=True)
class ProjectGroup:
    """A project plus the prompts under it, for grouped view."""

    project: str
    gists: list[PromptGist]
    usage: TokenUsage = field(init=False)
    cost_usd: float | None = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", summarize(self.gists))
        fallback_model = project_pricing_model(self.gists)
        costs = [
            estimate_cost_usd(g.model, g.usage, fallback_model=fallback_model)
            for g in self.gists
        ]
        object.__setattr__(
            self,
            "cost_usd",
            None if any(cost is None for cost in costs) else sum(costs),
        )

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
    cost: str
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
    cost_line: str
    text: str
    event_type: str = ""
    role: str = ""
    message_id: str = ""
    events: list[AssociatedEvent] = field(default_factory=list)


@dataclass(frozen=True)
class GroupDetailView:
    kind: Literal["header"]
    project: str
    count: int
    oldest: str | None
    newest: str | None
    average_tokens: str
    usage_line: str
    cost_line: str


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
        sort_by_cost: bool = False,
        since=None,
        until=None,
    ) -> None:
        self.gists = gists
        self.grouped = grouped
        self.collapsed = collapsed or set()
        self.sort_by_cost = sort_by_cost
        self.since = since
        self.until = until
        self._project_pricing_models = pricing_models_by_project(gists)

    @property
    def subtitle(self) -> str:
        mode = "grouped" if self.grouped else "flat"
        order = " · cost desc" if self.sort_by_cost else ""
        total = summarize(self.gists)
        period = self._format_period()
        period_str = f" · {period}" if period else ""
        return (
            f"{len(self.gists)} prompts · {format_tokens(total.total)} tokens "
            f"· {mode}{order}{period_str}"
        )

    def _format_period(self) -> str | None:
        if self.since is None and self.until is None:
            if not self.gists:
                return None
            # Derive from actual gist timestamps
            timestamps = [g.timestamp for g in self.gists]
            start = min(timestamps)
            end = max(timestamps)
        else:
            if self.gists:
                timestamps = [g.timestamp for g in self.gists]
                start = self.since or min(timestamps)
                end = self.until or max(timestamps)
            else:
                start = self.since
                end = self.until
        parts = []
        if start is not None:
            parts.append(to_local(start).strftime("%Y-%m-%d"))
        if end is not None:
            if parts:
                parts.append(to_local(end).strftime("%Y-%m-%d"))
            else:
                parts.append(f"until {to_local(end).strftime('%Y-%m-%d')}")
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0] if self.since is None else f"since {parts[0]}"
        return f"{parts[0]} → {parts[1]}"

    @property
    def projects(self) -> set[str]:
        return {g.project for g in self.gists}

    def table_rows(self) -> list[TableRowView]:
        if self.grouped:
            return self._grouped_rows()
        gists = (
            self._sort_gists_by_cost(self.gists) if self.sort_by_cost else self.gists
        )
        return [
            self._gist_row(
                g,
                show_project=True,
                indent=False,
                fallback_model=self._project_pricing_models.get(g.project),
            )
            for g in gists
        ]

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
        groups = group_by_project(self.gists)
        if self.sort_by_cost:
            groups = sorted(groups, key=lambda group: cost_sort_key(group.cost_usd))
        for group in groups:
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
                    cost=format_cost(group.cost_usd),
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
            group_gists = (
                self._sort_gists_by_cost(group.gists)
                if self.sort_by_cost
                else group.gists
            )
            rows.extend(
                self._gist_row(
                    g,
                    show_project=False,
                    indent=True,
                    fallback_model=self._project_pricing_models.get(g.project),
                )
                for g in group_gists
            )
        return rows

    def _sort_gists_by_cost(self, gists: list[PromptGist]) -> list[PromptGist]:
        return sorted(
            gists,
            key=lambda g: cost_sort_key(
                estimate_cost_usd(
                    g.model,
                    g.usage,
                    fallback_model=self._project_pricing_models.get(g.project),
                )
            ),
        )

    @staticmethod
    def _gist_row(
        g: PromptGist,
        *,
        show_project: bool,
        indent: bool,
        fallback_model: str | None = None,
    ) -> TableRowView:
        gist = g.gist_preview(68)
        return TableRowView(
            kind="gist",
            project=g.project,
            time=to_local(g.timestamp).strftime("%m-%d %H:%M"),
            project_label=short_project(g.project) if show_project else "",
            tokens=format_tokens(g.usage.total),
            cost=format_cost(
                estimate_cost_usd(g.model, g.usage, fallback_model=fallback_model)
            ),
            input_tokens=format_tokens(g.usage.input_tokens),
            output_tokens=format_tokens(g.usage.output_tokens),
            cache_write_tokens=format_tokens(g.usage.cache_creation_input_tokens),
            cache_read_tokens=format_tokens(g.usage.cache_read_input_tokens),
            model=g.model.replace("claude-", "") or "—",
            gist=("  " + gist) if indent else gist,
            entry=g,
        )

    def _prompt_detail(self, g: PromptGist) -> PromptDetailView:
        fallback_model = self._project_pricing_models.get(g.project)
        return PromptDetailView(
            kind="gist",
            when=to_local(g.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            project=g.project,
            model=g.model or "—",
            session_id=g.session_id[:8],
            usage_line=usage_line(g.usage),
            cost_line=cost_line(g.model, g.usage, fallback_model=fallback_model),
            text=g.text,
            event_type=g.event_type,
            role=g.role,
            message_id=g.message_id,
            events=g.events,
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
            cost_line=f"  estimated={format_cost(group.cost_usd)}",
        )


def usage_line(usage: TokenUsage) -> str:
    return (
        f"  total={format_tokens(usage.total)}  "
        f"in={format_tokens(usage.input_tokens)}  "
        f"out={format_tokens(usage.output_tokens)}  "
        f"cache_w={format_tokens(usage.cache_creation_input_tokens)}  "
        f"cache_r={format_tokens(usage.cache_read_input_tokens)}"
    )


def project_pricing_model(gists: list[PromptGist]) -> str | None:
    counts = Counter(g.model for g in gists if has_pricing(g.model))
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def pricing_models_by_project(gists: list[PromptGist]) -> dict[str, str]:
    buckets: dict[str, list[PromptGist]] = {}
    for gist in gists:
        buckets.setdefault(gist.project, []).append(gist)
    return {
        project: model
        for project, project_gists in buckets.items()
        if (model := project_pricing_model(project_gists)) is not None
    }


def cost_sort_key(cost: float | None) -> tuple[int, float]:
    if cost is None:
        return (1, 0.0)
    return (0, -cost)


def cost_line(
    model: str, usage: TokenUsage, *, fallback_model: str | None = None
) -> str:
    cost = estimate_cost_usd(model, usage, fallback_model=fallback_model)
    suffix = ""
    if cost is not None and fallback_model is not None and not has_pricing(model):
        suffix = f" using {fallback_model}"
    return f"  estimated={format_cost(cost)}{suffix}"
