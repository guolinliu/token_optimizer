"""Command-line entry point for claude-gists."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .history import default_projects_dir, load_gists, summarize
from .models import format_tokens, to_local


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-gists",
        description="Browse recent Claude prompts and token consumption "
        "from local Claude Code history.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max number of recent prompts to show (default: 200).",
    )
    parser.add_argument(
        "--project",
        metavar="SUBSTR",
        default=None,
        help="Only show prompts whose project label contains this substring.",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Override the projects transcript directory "
        "(default: ~/.claude/projects).",
    )
    parser.add_argument(
        "--group",
        action="store_true",
        help="Start in grouped-by-project view (toggle with 'g' in the TUI).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print a plain-text table instead of launching the TUI.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def _print_list(projects_dir, project_filter, limit, grouped) -> int:
    gists = load_gists(
        projects_dir, project_filter=project_filter, limit=limit
    )
    if not gists:
        print(f"No prompts found in {projects_dir or default_projects_dir()}")
        return 0

    if grouped:
        # Imported here so --list works without the textual dependency.
        from .app import group_by_project

        for grp in group_by_project(gists):
            print(
                f"\n▼ {grp.project}  "
                f"{format_tokens(grp.usage.total)}  ({grp.count} prompts)"
            )
            for g in grp.gists:
                ts = to_local(g.timestamp).strftime("%m-%d %H:%M")
                print(
                    f"  {ts}  {format_tokens(g.usage.total):>8}  "
                    f"{g.gist_preview(80)}"
                )
    else:
        for g in gists:
            ts = to_local(g.timestamp).strftime("%m-%d %H:%M")
            print(
                f"{ts}  {g.project[:18]:<18}  "
                f"{format_tokens(g.usage.total):>8}  {g.gist_preview(80)}"
            )

    total = summarize(gists)
    print(f"\n{len(gists)} prompts · {format_tokens(total.total)} total tokens")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        return _print_list(args.dir, args.project, args.limit, args.group)

    # Imported lazily so --list / --help work without textual installed.
    from .app import run

    run(
        args.dir,
        project_filter=args.project,
        limit=args.limit,
        grouped=args.group,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
