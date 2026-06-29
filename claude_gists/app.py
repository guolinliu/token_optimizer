"""Textual TUI for browsing prompt gists and token consumption."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from .history import load_gists
from .models import PromptGist
from .viewmodel import (
    DetailView,
    EmptyDetailView,
    GistsViewModel,
    GroupDetailView,
    ProjectGroup,
    PromptDetailView,
    TableRowView,
    group_by_project,
)

# A table row is either a prompt or a project group header.
RowEntry = tuple[str, object]  # ("gist", PromptGist) | ("header", ProjectGroup)


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
        projects = self._view_model().projects
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

    def _view_model(self) -> GistsViewModel:
        return GistsViewModel(
            self._gists,
            grouped=self._grouped,
            collapsed=self._collapsed,
        )

    def _populate(self, focus_project: str | None = None) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        view_model = self._view_model()
        rows = view_model.table_rows()
        self._rows = [(row.kind, row.entry) for row in rows]
        for index, row in enumerate(rows):
            self._add_table_row(table, row, index=index)

        self.sub_title = view_model.subtitle

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

    def _add_table_row(
        self, table: DataTable, row: TableRowView, *, index: int
    ) -> None:
        # Rich Text cells render literally — never parsed as console markup,
        # so prompts/projects containing "[" don't crash the table.
        if row.is_header:
            project = Text(row.project_label, style="bold")
            tokens = Text(row.tokens, style="bold")
            input_tokens = Text(row.input_tokens, style="bold")
            output_tokens = Text(row.output_tokens, style="bold")
            cache_write_tokens = Text(row.cache_write_tokens, style="bold")
            cache_read_tokens = Text(row.cache_read_tokens, style="bold")
            gist = Text(row.gist, style="italic dim")
        else:
            project = Text(row.project_label)
            tokens = row.tokens
            input_tokens = row.input_tokens
            output_tokens = row.output_tokens
            cache_write_tokens = row.cache_write_tokens
            cache_read_tokens = row.cache_read_tokens
            gist = Text(row.gist)

        table.add_row(
            row.time,
            project,
            tokens,
            input_tokens,
            output_tokens,
            cache_write_tokens,
            cache_read_tokens,
            row.model,
            gist,
            key=str(index),
        )

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
        detail.update(self._detail_text(self._view_model().detail_for_row(row_index)))

    def _detail_text(self, view: DetailView) -> Text:
        if isinstance(view, EmptyDetailView):
            return Text(view.message)
        if isinstance(view, GroupDetailView):
            return self._group_detail_text(view)
        return self._prompt_detail_text(view)

    def _prompt_detail_text(self, view: PromptDetailView) -> Text:
        # Build a Rich Text rather than a markup string: styles are attached as
        # spans, so the prompt body (which may contain "[" / "]") is rendered
        # verbatim instead of being parsed as Textual console markup.
        body = Text()
        body.append(view.when, style="bold")
        body.append("  ·  ")
        body.append(view.project, style="cyan")
        body.append("  ·  ")
        body.append(view.model)
        body.append(f"  ·  session {view.session_id}\n")
        body.append("Tokens", style="bold")
        body.append(view.usage_line + "\n\n")
        body.append(view.text)
        return body

    def _group_detail_text(self, view: GroupDetailView) -> Text:
        body = Text()
        body.append(view.project, style="bold cyan")
        body.append(f"\n{view.count} prompts")
        if view.oldest and view.newest:
            body.append(f"  ·  {view.oldest} → {view.newest}")
        body.append(f"  ·  avg {view.average_tokens}/prompt\n")
        body.append("Tokens", style="bold")
        body.append(view.usage_line)
        return body


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
