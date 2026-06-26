"""Textual TUI for browsing prompt gists and token consumption."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from .history import load_gists, summarize
from .models import PromptGist, TokenUsage, format_tokens, to_local

# A table row is either a prompt or a project group header.
RowEntry = tuple[str, object]  # ("gist", PromptGist) | ("header", ProjectGroup)


class ProjectGroup:
    """A project plus the prompts under it, for grouped view."""

    def __init__(self, project: str, gists: list[PromptGist]) -> None:
        self.project = project
        self.gists = gists
        self.usage = summarize(gists)

    @property
    def count(self) -> int:
        return len(self.gists)


def group_by_project(gists: list[PromptGist]) -> list[ProjectGroup]:
    """Group gists by project, preserving newest-first project order.

    The project containing the most recent prompt comes first; prompts inside a
    group keep their (already newest-first) order.
    """
    order: list[str] = []
    buckets: dict[str, list[PromptGist]] = {}
    for g in gists:
        if g.project not in buckets:
            buckets[g.project] = []
            order.append(g.project)
        buckets[g.project].append(g)
    return [ProjectGroup(p, buckets[p]) for p in order]


def _short_project(project: str, width: int = 24) -> str:
    return project if len(project) <= width else "…" + project[-(width - 1) :]


class GistsApp(App):
    """List recent Claude prompts with their token cost; drill into details."""

    CSS = """
    #detail {
        height: 40%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #table {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
        Binding("g", "toggle_group", "Group by project"),
        Binding("space", "toggle_fold", "Fold group"),
        Binding("z", "toggle_all_folds", "Fold all"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(
        self,
        projects_dir: Path | None = None,
        *,
        project_filter: str | None = None,
        limit: int | None = 200,
        grouped: bool = False,
    ) -> None:
        super().__init__()
        self._projects_dir = projects_dir
        self._project_filter = project_filter
        self._limit = limit
        self._grouped = grouped
        self._gists: list[PromptGist] = []
        # Parallel to the visible table rows; maps a row to what it represents.
        self._rows: list[RowEntry] = []
        # Projects whose prompt rows are currently folded away (grouped view).
        self._collapsed: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#table", DataTable)
        table.add_columns(
            "Time", "Project", "Tokens", "In", "Out",
            "CacheW", "CacheR", "Model", "Gist",
        )
        self.action_reload()

    # ------------------------------------------------------------------ actions

    def action_reload(self) -> None:
        """Reload gists from disk and repopulate the table."""
        self._gists = load_gists(
            self._projects_dir,
            project_filter=self._project_filter,
            limit=self._limit,
        )
        self._populate()

    def action_toggle_group(self) -> None:
        """Flip between flat and grouped-by-project view (no disk reload)."""
        self._grouped = not self._grouped
        self._populate()

    def action_toggle_fold(self) -> None:
        """Fold/unfold the project of the highlighted row (grouped view only)."""
        if not self._grouped:
            return
        project = self._project_at_cursor()
        if project is None:
            return
        if project in self._collapsed:
            self._collapsed.discard(project)
        else:
            self._collapsed.add(project)
        self._populate(focus_project=project)

    def action_toggle_all_folds(self) -> None:
        """Collapse every project, or expand all if everything is collapsed."""
        if not self._grouped:
            return
        projects = {g.project for g in self._gists}
        if projects and projects <= self._collapsed:
            self._collapsed.clear()
        else:
            self._collapsed = projects
        self._populate()

    def _project_at_cursor(self) -> str | None:
        table = self.query_one("#table", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._rows):
            return None
        kind, obj = self._rows[row]
        return obj.project  # ProjectGroup and PromptGist both expose .project

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Enter on a group header (or any grouped row) toggles its fold.
        self.action_toggle_fold()

    def action_cursor_down(self) -> None:
        self.query_one("#table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#table", DataTable).action_cursor_up()

    # ------------------------------------------------------------------ rows

    def _populate(self, focus_project: str | None = None) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        self._rows = []
        if self._grouped:
            self._populate_grouped(table)
        else:
            self._populate_flat(table)

        total = summarize(self._gists)
        mode = "grouped" if self._grouped else "flat"
        self.sub_title = (
            f"{len(self._gists)} prompts · {format_tokens(total.total)} tokens "
            f"· {mode}"
        )

        # Keep the cursor on the project the user just folded/unfolded.
        target = 0
        if focus_project is not None:
            for idx, (kind, obj) in enumerate(self._rows):
                if kind == "header" and obj.project == focus_project:
                    target = idx
                    break
        if self._rows:
            table.move_cursor(row=target)
        self._render_detail(target)

    def _add_gist_row(
        self, table: DataTable, g: PromptGist, *, show_project: bool, indent: bool
    ) -> None:
        ts = to_local(g.timestamp).strftime("%m-%d %H:%M")
        project = Text(_short_project(g.project)) if show_project else Text("")
        gist = g.gist_preview(68)
        # Rich Text cells render literally — never parsed as console markup,
        # so prompts/projects containing "[" don't crash the table.
        table.add_row(
            ts,
            project,
            format_tokens(g.usage.total),
            format_tokens(g.usage.input_tokens),
            format_tokens(g.usage.output_tokens),
            format_tokens(g.usage.cache_creation_input_tokens),
            format_tokens(g.usage.cache_read_input_tokens),
            g.model.replace("claude-", "") or "—",
            Text(("  " + gist) if indent else gist),
            key=str(len(self._rows)),
        )
        self._rows.append(("gist", g))

    def _populate_flat(self, table: DataTable) -> None:
        for g in self._gists:
            self._add_gist_row(table, g, show_project=True, indent=False)

    def _populate_grouped(self, table: DataTable) -> None:
        for grp in group_by_project(self._gists):
            collapsed = grp.project in self._collapsed
            marker = "▶" if collapsed else "▼"
            count = f"{grp.count} prompts" + (" (folded)" if collapsed else "")
            table.add_row(
                "",
                Text(f"{marker} " + _short_project(grp.project), style="bold"),
                Text(format_tokens(grp.usage.total), style="bold"),
                Text(format_tokens(grp.usage.input_tokens), style="bold"),
                Text(format_tokens(grp.usage.output_tokens), style="bold"),
                Text(
                    format_tokens(grp.usage.cache_creation_input_tokens),
                    style="bold",
                ),
                Text(
                    format_tokens(grp.usage.cache_read_input_tokens),
                    style="bold",
                ),
                "",
                Text(count, style="italic dim"),
                key=str(len(self._rows)),
            )
            self._rows.append(("header", grp))
            if collapsed:
                continue
            for g in grp.gists:
                self._add_gist_row(table, g, show_project=False, indent=True)

    # ------------------------------------------------------------------ detail

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        try:
            index = int(event.row_key.value) if event.row_key.value else 0
        except (TypeError, ValueError):
            index = 0
        self._render_detail(index)

    def _render_detail(self, row_index: int) -> None:
        detail = self.query_one("#detail", Static)
        if not self._rows or row_index < 0 or row_index >= len(self._rows):
            detail.update("No prompts found in local history.")
            return
        kind, obj = self._rows[row_index]
        if kind == "header":
            detail.update(self._group_detail(obj))  # type: ignore[arg-type]
        else:
            detail.update(self._gist_detail(obj))  # type: ignore[arg-type]

    def _gist_detail(self, g: PromptGist) -> Text:
        u = g.usage
        when = to_local(g.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        # Build a Rich Text rather than a markup string: styles are attached as
        # spans, so the prompt body (which may contain "[" / "]") is rendered
        # verbatim instead of being parsed as Textual console markup.
        body = Text()
        body.append(when, style="bold")
        body.append("  ·  ")
        body.append(g.project, style="cyan")
        body.append("  ·  ")
        body.append(g.model or "—")
        body.append(f"  ·  session {g.session_id[:8]}\n")
        body.append("Tokens", style="bold")
        body.append(self._usage_line(u) + "\n\n")
        body.append(g.text)
        return body

    def _group_detail(self, grp: ProjectGroup) -> Text:
        gists = grp.gists
        newest = to_local(gists[0].timestamp).strftime("%Y-%m-%d %H:%M")
        oldest = to_local(gists[-1].timestamp).strftime("%Y-%m-%d %H:%M")
        body = Text()
        body.append(grp.project, style="bold cyan")
        body.append(f"\n{grp.count} prompts")
        if grp.count:
            body.append(f"  ·  {oldest} → {newest}")
        avg = grp.usage.total // grp.count if grp.count else 0
        body.append(f"  ·  avg {format_tokens(avg)}/prompt\n")
        body.append("Tokens", style="bold")
        body.append(self._usage_line(grp.usage))
        return body

    @staticmethod
    def _usage_line(u: TokenUsage) -> str:
        return (
            f"  total={format_tokens(u.total)}  "
            f"in={format_tokens(u.input_tokens)}  "
            f"out={format_tokens(u.output_tokens)}  "
            f"cache_w={format_tokens(u.cache_creation_input_tokens)}  "
            f"cache_r={format_tokens(u.cache_read_input_tokens)}"
        )


def run(
    projects_dir: Path | None = None,
    *,
    project_filter: str | None = None,
    limit: int | None = 200,
    grouped: bool = False,
) -> None:
    GistsApp(
        projects_dir,
        project_filter=project_filter,
        limit=limit,
        grouped=grouped,
    ).run()
