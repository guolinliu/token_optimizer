from pathlib import Path

from claude_gists.history import load_gists
from claude_gists.viewmodel import (
    EmptyDetailView,
    GistsViewModel,
    GroupDetailView,
    PromptDetailView,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_view_model_exposes_flat_rows_without_textual():
    gists = load_gists(FIXTURES)
    view_model = GistsViewModel(gists)

    rows = view_model.table_rows()

    assert [row.kind for row in rows] == ["gist", "gist", "gist"]
    assert rows[0].project == "sample-project"
    assert rows[0].tokens == "15"
    assert rows[0].model == "opus-4-8"
    assert rows[0].gist == "need textual>=1.1.3 and [E13 OK] done [user@host ~]$"
    assert view_model.subtitle == "3 prompts · 880 tokens · flat"


def test_view_model_exposes_grouped_and_collapsed_rows():
    gists = load_gists(FIXTURES)
    view_model = GistsViewModel(
        gists,
        grouped=True,
        collapsed={"sample-project"},
    )

    rows = view_model.table_rows()

    assert len(rows) == 1
    assert rows[0].kind == "header"
    assert rows[0].project_label == "▶ sample-project"
    assert rows[0].tokens == "880"
    assert rows[0].gist == "3 prompts (folded)"
    assert view_model.project_at_row(0) == "sample-project"


def test_view_model_exposes_detail_payloads():
    gists = load_gists(FIXTURES)

    prompt_detail = GistsViewModel(gists).detail_for_row(0)
    assert isinstance(prompt_detail, PromptDetailView)
    assert prompt_detail.project == "sample-project"
    assert prompt_detail.session_id == "sess-1"
    assert prompt_detail.usage_line == (
        "  total=15  in=10  out=5  cache_w=0  cache_r=0"
    )
    assert "[E13 OK]" in prompt_detail.text

    group_detail = GistsViewModel(gists, grouped=True).detail_for_row(0)
    assert isinstance(group_detail, GroupDetailView)
    assert group_detail.project == "sample-project"
    assert group_detail.count == 3
    assert group_detail.average_tokens == "293"
    assert group_detail.usage_line == (
        "  total=880  in=610  out=235  cache_w=10  cache_r=25"
    )

    empty_detail = GistsViewModel([]).detail_for_row(0)
    assert isinstance(empty_detail, EmptyDetailView)
