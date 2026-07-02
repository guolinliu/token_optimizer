"""Regression test: prompts containing markup-like brackets must not crash."""

import asyncio
from pathlib import Path

from textual.widgets import DataTable

from claude_gists.app import GistsApp

FIXTURES = Path(__file__).parent / "fixtures"


def test_group_by_project_preserves_order_and_totals():
    from claude_gists.history import load_gists
    from claude_gists.app import group_by_project

    gists = load_gists(FIXTURES)
    groups = group_by_project(gists)
    # Single fixture project; all prompts land in one group.
    assert [g.project for g in groups] == ["sample-project"]
    grp = groups[0]
    assert grp.count == 3
    assert grp.usage.total == 880  # 465 + 400 + 15


def test_toggle_group_adds_header_rows():
    from textual.widgets import DataTable

    async def scenario() -> None:
        app = GistsApp(projects_dir=FIXTURES)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#table", DataTable)
            assert table.row_count == 3  # flat: one row per prompt
            await pilot.press("g")  # toggle grouped
            await pilot.pause()
            # 1 project header + 3 prompt rows.
            assert table.row_count == 4
            assert app._rows[0][0] == "header"
            # Highlight the header row -> project summary, no crash.
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("g")  # back to flat
            await pilot.pause()
            assert table.row_count == 3

    asyncio.run(scenario())


def test_fold_collapses_and_expands_group():
    from textual.widgets import DataTable

    async def scenario() -> None:
        app = GistsApp(projects_dir=FIXTURES)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("g")  # grouped: 1 header + 3 prompts
            await pilot.pause()
            table = app.query_one("#table", DataTable)
            assert table.row_count == 4

            # Cursor is on the header; fold it -> only the header remains.
            await pilot.press("space")
            await pilot.pause()
            assert table.row_count == 1
            assert app._rows[0][0] == "header"
            assert "sample-project" in app._collapsed

            # Unfold via Enter (row selected) -> prompts come back.
            await pilot.press("enter")
            await pilot.pause()
            assert table.row_count == 4
            assert "sample-project" not in app._collapsed

    asyncio.run(scenario())


def test_fold_all_toggles_every_group():
    from textual.widgets import DataTable

    async def scenario() -> None:
        app = GistsApp(projects_dir=FIXTURES)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            table = app.query_one("#table", DataTable)
            await pilot.press("z")  # collapse all
            await pilot.pause()
            assert table.row_count == 1  # only the single project's header
            await pilot.press("z")  # expand all
            await pilot.pause()
            assert table.row_count == 4

    asyncio.run(scenario())


def test_fold_all_shortcut_collapses_groups():
    from textual.widgets import DataTable

    async def scenario() -> None:
        app = GistsApp(projects_dir=FIXTURES)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            table = app.query_one("#table", DataTable)
            assert table.row_count == 4

            await pilot.press("f")
            await pilot.pause()
            assert table.row_count == 1
            assert "sample-project" in app._collapsed

            # Unlike z, f is a one-way fold-all shortcut.
            await pilot.press("f")
            await pilot.pause()
            assert table.row_count == 1
            assert "sample-project" in app._collapsed

    asyncio.run(scenario())


def test_cost_sort_shortcut_toggles_sort_mode():
    from textual.widgets import DataTable

    async def scenario() -> None:
        app = GistsApp(projects_dir=FIXTURES)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#table", DataTable)
            assert table.row_count == 3
            assert app._sort_by_cost is False

            await pilot.press("c")
            await pilot.pause()
            assert table.row_count == 3
            assert app._sort_by_cost is True

            await pilot.press("c")
            await pilot.pause()
            assert table.row_count == 3
            assert app._sort_by_cost is False

    asyncio.run(scenario())


def test_render_detail_handles_bracket_text():
    """Highlighting a bracket-laden prompt renders literally, no MarkupError.

    The newest fixture prompt is "need textual>=1.1.3 and [E13 OK] done
    [user@host ~]$" — its brackets previously triggered rich MarkupError.
    """

    async def scenario() -> None:
        app = GistsApp(projects_dir=FIXTURES)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#table", DataTable).row_count == 3
            # Walk every row; each highlight calls _render_detail(). Before the
            # markup-escaping fix, the bracket-laden prompt raised MarkupError
            # here. Reaching the end without an exception is the regression
            # guarantee.
            await pilot.press("down")
            await pilot.press("down")
            await pilot.pause()

    asyncio.run(scenario())
