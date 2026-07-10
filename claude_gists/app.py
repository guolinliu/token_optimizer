"""Textual TUI for browsing prompt gists and token consumption."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Header, Static

from .history import load_gists
from .models import PromptGist, format_tokens
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
    #detail-container {
        height: 40%;
        border: round $accent;
        padding: 0 1;
        background: $surface;
    }
    #detail {
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
        Binding("c", "toggle_cost_sort", "Sort by cost"),
        Binding("space", "toggle_fold", "Fold group"),
        Binding("f", "fold_all", "Fold all"),
        Binding("z", "toggle_all_folds", "Fold all"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(
        self,
        projects_dir: Path | None = None,
        *,
        project_filter: str | None = None,
        limit: int | None = None,
        grouped: bool = False,
        since=None,
        until=None,
    ) -> None:
        super().__init__()
        self._projects_dir = projects_dir
        self._project_filter = project_filter
        self._limit = limit
        self._grouped = grouped
        self._since = since
        self._until = until
        self._sort_by_cost = False
        self._gists: list[PromptGist] = []
        # Parallel to the visible table rows; maps a row to what it represents.
        self._rows: list[RowEntry] = []
        # Projects whose prompt rows are currently folded away (grouped view).
        self._collapsed: set[str] = set()
        # Whether we've applied the default "expand only first group" state.
        self._default_collapse_applied = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="detail-container"):
                yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.action_reload()

    # ------------------------------------------------------------------ actions

    def action_reload(self) -> None:
        """Reload gists from disk and repopulate the table."""
        self._gists = load_gists(
            self._projects_dir,
            project_filter=self._project_filter,
            limit=self._limit,
            since=self._since,
            until=self._until,
        )
        self._populate()

    def action_toggle_group(self) -> None:
        """Flip between flat and grouped-by-project view (no disk reload)."""
        self._grouped = not self._grouped
        self._populate()

    def action_toggle_cost_sort(self) -> None:
        """Toggle cost-descending ordering."""
        self._sort_by_cost = not self._sort_by_cost
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

    def action_fold_all(self) -> None:
        """Collapse every project in grouped view."""
        if not self._grouped:
            return
        self._collapsed = self._view_model().projects
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
            sort_by_cost=self._sort_by_cost,
            since=self._since,
            until=self._until,
        )

    def _populate(self, focus_project: str | None = None) -> None:
        table = self.query_one("#table", DataTable)
        # By default, expand only the first group and collapse the rest.
        if self._grouped and not self._default_collapse_applied:
            ordered_projects = [g.project for g in group_by_project(self._gists)]
            if self._sort_by_cost:
                # Reorder by cost descending to match the view model's sorting.
                groups = group_by_project(self._gists)
                from .viewmodel import cost_sort_key
                from .pricing import estimate_cost_usd
                from .viewmodel import pricing_models_by_project

                project_pricing = pricing_models_by_project(self._gists)

                def _group_cost(g):
                    costs = [
                        estimate_cost_usd(
                            p.model,
                            p.usage,
                            fallback_model=project_pricing.get(p.project),
                        )
                        for p in g.gists
                    ]
                    total = None if any(c is None for c in costs) else sum(costs)
                    return cost_sort_key(total)

                groups = sorted(groups, key=_group_cost)
                ordered_projects = [g.project for g in groups]
            if ordered_projects:
                first, *rest = ordered_projects
                self._collapsed = set(rest)
            self._default_collapse_applied = True
        view_model = self._view_model()
        rows = view_model.table_rows()
        # Compute dynamic target width for the Gist column in grouped view
        # so the prompt count stays right-aligned even when gists are long.
        if self._grouped:
            max_gist_len = 0
            for r in rows:
                if r.is_header:
                    max_gist_len = max(max_gist_len, len(r.project_label))
                else:
                    max_gist_len = max(max_gist_len, len(r.gist))
            # Add room for the count string (e.g. "123 prompts") and padding.
            # Use at least 50 chars, or the max content length plus 15.
            self._gist_column_target_width = max(50, max_gist_len + 15)
        else:
            self._gist_column_target_width = 50
        # Clear table and re-add columns. In grouped view, set a fixed width
        # for the Gist column to prevent it from shrinking when headers scroll
        # out of view (DataTable may recalculate widths based on visible rows).
        # Using a fixed width ensures the column stays wide enough for the
        # padded header content even when only prompt rows are visible.
        table.clear(columns=True)
        if self._grouped:
            # In grouped view, Project column is renamed to Gist and Gist column removed.
            # Set width on the Gist column to keep it wide enough for the
            # padded header content even when only prompt rows are visible.
            # Use the dynamic target width, but ensure a minimum of 80 chars.
            # Also set a fixed width for the Time column to prevent truncation;
            # time strings are "MM-DD HH:MM" (11 chars), so 12 chars is sufficient.
            gist_width = max(80, self._gist_column_target_width)
            table.add_column("Time", width=12)
            table.add_column("Gist", width=gist_width)
            table.add_column("Tokens")
            table.add_column("Cost")
            table.add_column("In")
            table.add_column("Out")
            table.add_column("CacheW")
            table.add_column("CacheR")
            table.add_column("Model")
        else:
            # Flat view: set Time column width to prevent truncation.
            table.add_column("Time", width=12)
            table.add_column("Project")
            table.add_column("Tokens")
            table.add_column("Cost")
            table.add_column("In")
            table.add_column("Out")
            table.add_column("CacheW")
            table.add_column("CacheR")
            table.add_column("Model")
            table.add_column("Gist")
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
            cost = Text(row.cost, style="bold")
            input_tokens = Text(row.input_tokens, style="bold")
            output_tokens = Text(row.output_tokens, style="bold")
            cache_write_tokens = Text(row.cache_write_tokens, style="bold")
            cache_read_tokens = Text(row.cache_read_tokens, style="bold")
            gist = Text(row.gist, style="italic dim")
        else:
            project = Text(row.project_label)
            tokens = row.tokens
            cost = row.cost
            input_tokens = row.input_tokens
            output_tokens = row.output_tokens
            cache_write_tokens = row.cache_write_tokens
            cache_read_tokens = row.cache_read_tokens
            gist = Text(row.gist)

        if self._grouped:
            # In grouped view, the Project column is renamed to Gist and the Gist
            # column is removed. Header rows keep the group name in the Gist column;
            # prompt rows show the gist preview in the Gist column.
            if row.is_header:
                # row.project_label contains the group name on the first line,
                # followed by the included prompts with times on subsequent lines.
                # Split into first line (group name) and rest (prompts).
                lines = row.project_label.split("\n")
                first_line = lines[0] if lines else ""
                rest_lines = lines[1:] if len(lines) > 1 else []
                # Create the project Text with only the first line bold.
                project = Text(first_line, style="bold")
                # Put the prompt count to the right of the first line, right-aligned
                # within the Gist column. We pad with spaces to push the count
                # to the far right while keeping the group name left-aligned.
                count_text = row.gist or ""
                target_width = getattr(self, "_gist_column_target_width", 50)
                group_len = len(first_line)
                count_len = len(count_text)
                spaces_needed = max(2, target_width - group_len - count_len)
                project.append(" " * spaces_needed)
                project.append(count_text, style="italic dim")
                # Append the included prompts (with time in front) below the group name.
                if rest_lines:
                    project.append("\n")
                    project.append("\n".join(rest_lines))
            else:
                # row.gist contains the indented gist preview ("  " + preview);
                # keep the indent so it's visually under the project header.
                if isinstance(gist, Text):
                    gist_text = gist.plain
                else:
                    gist_text = row.gist if isinstance(row.gist, str) else ""
                project = Text(gist_text)
                # Pad prompt rows to the target width as well, so the Gist column
                # stays wide even when headers scroll out of view. This prevents
                # columns from narrowing when hovering over prompt rows after
                # expanding a group by clicking (which may move the cursor and
                # scroll the header out of view).
                target_width = getattr(self, "_gist_column_target_width", 50)
                if len(gist_text) < target_width:
                    project.append(" " * (target_width - len(gist_text)))
            table.add_row(
                row.time,
                project,
                tokens,
                cost,
                input_tokens,
                output_tokens,
                cache_write_tokens,
                cache_read_tokens,
                row.model,
                key=str(index),
            )
        else:
            table.add_row(
                row.time,
                project,
                tokens,
                cost,
                input_tokens,
                output_tokens,
                cache_write_tokens,
                cache_read_tokens,
                row.model,
                gist,
                key=str(index),
            )

    # ------------------------------------------------------------------ detail

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
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
        body.append(view.usage_line + "\n")
        body.append("Cost", style="bold")
        body.append(view.cost_line + "\n\n")

        # Display each associated event with its type, role, and content.
        # Token usage is shown on a newline below each row.
        # Group assistant events by message_id to avoid duplicate token usage display.
        from collections import defaultdict

        # Group events by message_id for assistant role, keep others separate
        grouped_events: dict[str, list] = defaultdict(list)
        standalone_events = []

        for event in view.events:
            if event.role == "assistant" and event.message_id:
                grouped_events[event.message_id].append(event)
            else:
                standalone_events.append(event)

        # Display standalone events first (user events, etc.)
        for event in standalone_events:
            if event.event_type:
                body.append(event.event_type, style="bold")
            if event.role:
                body.append(f" ({event.role})", style="cyan")
            if event.content:
                body.append(f"  {event.content}")
            body.append("\n")
            if event.usage is not None:
                usage_str = (
                    f"  total={format_tokens(event.usage.total)}  "
                    f"in={format_tokens(event.usage.input_tokens)}  "
                    f"out={format_tokens(event.usage.output_tokens)}  "
                    f"cache_w={format_tokens(event.usage.cache_creation_input_tokens)}  "
                    f"cache_r={format_tokens(event.usage.cache_read_input_tokens)}"
                )
                body.append(usage_str, style="dim")
                body.append("\n")

        # Display grouped assistant events
        for message_id, events in grouped_events.items():
            if not events:
                continue

            # Use the first event for group header info
            first = events[0]
            # Get model from first event or fall back to view model
            model = first.model or view.model
            model_short = model.replace("claude-", "") if model else "—"

            # Get usage from first event that has it (they should all be the same)
            usage = None
            for e in events:
                if e.usage is not None:
                    usage = e.usage
                    break

            # Header: role · message_id · model
            if first.role:
                body.append(first.role, style="bold")
            if message_id:
                # Shorten message_id for display (e.g., msg_vrtx_011JN -> msg_vrtx_011JN)
                short_id = message_id[:20] if len(message_id) > 20 else message_id
                body.append(f" · {short_id}", style="cyan")
            if model_short:
                body.append(f" · {model_short}")
            body.append("\n")

            # Show token usage on its own line, following existing display strategy
            if usage is not None:
                usage_str = (
                    f"  total={format_tokens(usage.total)}  "
                    f"in={format_tokens(usage.input_tokens)}  "
                    f"out={format_tokens(usage.output_tokens)}  "
                    f"cache_w={format_tokens(usage.cache_creation_input_tokens)}  "
                    f"cache_r={format_tokens(usage.cache_read_input_tokens)}"
                )
                body.append(usage_str, style="dim")
                body.append("\n")

            # Display each event in the group as a tree branch
            for i, event in enumerate(events):
                is_last = i == len(events) - 1
                branch = "     └ " if is_last else "     ├ "

                body.append(branch, style="dim")

                # Show event type (thinking, text, tool_use, etc.)
                if event.event_type:
                    body.append(event.event_type, style="bold")
                else:
                    body.append("event", style="bold")

                # Show content preview
                if event.content:
                    # Truncate long content for preview
                    content = event.content
                    if len(content) > 60:
                        content = content[:57] + "..."
                    # Replace newlines with spaces for single-line preview
                    content = " ".join(content.split())
                    body.append(f'      "{content}"', style="dim")
                elif event.event_type == "tool_use":
                    # For tool_use without content, try to show tool name
                    body.append("  ", style="dim")

                body.append("\n")

        return body

    def _group_detail_text(self, view: GroupDetailView) -> Text:
        body = Text()
        body.append(view.project, style="bold cyan")
        body.append(f"\n{view.count} prompts")
        if view.oldest and view.newest:
            body.append(f"  ·  {view.oldest} → {view.newest}")
        body.append(f"  ·  avg {view.average_tokens}/prompt\n")
        body.append("Tokens", style="bold")
        body.append(view.usage_line + "\n")
        body.append("Cost", style="bold")
        body.append(view.cost_line)
        # Display included user prompts with time in front.
        if view.prompts:
            body.append("\n\n")
            body.append("Prompts", style="bold")
            body.append("\n")
            for time_str, gist in view.prompts:
                body.append(f"{time_str}  ", style="dim")
                body.append(gist)
                body.append("\n")
        return body


def run(
    projects_dir: Path | None = None,
    *,
    project_filter: str | None = None,
    limit: int | None = None,
    grouped: bool = False,
    since=None,
    until=None,
) -> None:
    GistsApp(
        projects_dir,
        project_filter=project_filter,
        limit=limit,
        grouped=grouped,
        since=since,
        until=until,
    ).run()
