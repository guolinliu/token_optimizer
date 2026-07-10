"""Command-line entry point for claude-gists."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

from . import __version__
from .history import default_projects_dir, load_gists, summarize
from .models import format_tokens, to_local
from .pricing import estimate_cost_usd, format_cost
from .viewmodel import group_by_project, pricing_models_by_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-gists",
        description="Browse recent Claude prompts and token consumption "
        "from local Claude Code history.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of recent prompts to show (optional cap after time filtering).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=28,
        help="Only load prompts from the last N days (default: 28). Use 0 to disable time filtering.",
    )
    parser.add_argument(
        "--since",
        metavar="DATE",
        default=None,
        help="Only show prompts on or after this date (YYYY-MM-DD or ISO datetime). Overrides --days.",
    )
    parser.add_argument(
        "--until",
        metavar="DATE",
        default=None,
        help="Only show prompts on or before this date (YYYY-MM-DD or ISO datetime).",
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
        default=True,
        help="Start in grouped-by-project view (toggle with 'g' in the TUI). Enabled by default.",
    )
    parser.add_argument(
        "--no-group",
        dest="group",
        action="store_false",
        help="Start in flat view instead of grouped-by-project view.",
    )
    parser.add_argument(
        "--flat",
        dest="group",
        action="store_false",
        help="Alias for --no-group.",
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


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Accept YYYY-MM-DD or ISO datetime
        if len(s) == 10:
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise SystemExit(f"Invalid date format: {s!r}. Use YYYY-MM-DD or ISO datetime.")


def _format_period(since=None, until=None, gists=None) -> str | None:
    if since is None and until is None:
        if not gists:
            return None
        timestamps = [g.timestamp for g in gists]
        start = min(timestamps)
        end = max(timestamps)
    else:
        if gists:
            timestamps = [g.timestamp for g in gists]
            start = since or min(timestamps)
            end = until or max(timestamps)
        else:
            start = since
            end = until
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
        return parts[0] if since is None else f"since {parts[0]}"
    return f"{parts[0]} → {parts[1]}"


def _print_list(
    projects_dir, project_filter, limit, grouped, since=None, until=None
) -> int:
    gists = load_gists(
        projects_dir,
        project_filter=project_filter,
        limit=limit,
        since=since,
        until=until,
    )
    if not gists:
        print(f"No prompts found in {projects_dir or default_projects_dir()}")
        return 0

    period = _format_period(since, until, gists)
    if period:
        print(f"Showing prompts for period: {period}")

    project_pricing_models = pricing_models_by_project(gists)

    if grouped:
        for grp in group_by_project(gists):
            print(
                f"\n▼ {grp.project}  "
                f"{format_tokens(grp.usage.total)}  "
                f"{format_cost(grp.cost_usd)}  ({grp.count} prompts)"
            )
            for g in grp.gists:
                ts = to_local(g.timestamp).strftime("%m-%d %H:%M")
                cost = estimate_cost_usd(
                    g.model,
                    g.usage,
                    fallback_model=project_pricing_models.get(g.project),
                )
                print(
                    f"  {ts}  {format_tokens(g.usage.total):>8}  "
                    f"{format_cost(cost):>8}  "
                    f"{g.gist_preview(80)}"
                )
    else:
        for g in gists:
            ts = to_local(g.timestamp).strftime("%m-%d %H:%M")
            cost = estimate_cost_usd(
                g.model,
                g.usage,
                fallback_model=project_pricing_models.get(g.project),
            )
            print(
                f"{ts}  {g.project[:18]:<18}  "
                f"{format_tokens(g.usage.total):>8}  "
                f"{format_cost(cost):>8}  "
                f"{g.gist_preview(80)}"
            )

    total = summarize(gists)
    costs = [
        estimate_cost_usd(
            g.model,
            g.usage,
            fallback_model=project_pricing_models.get(g.project),
        )
        for g in gists
    ]
    total_cost = None if any(cost is None for cost in costs) else sum(costs)
    period_str = f" · {period}" if period else ""
    print(
        f"\n{len(gists)} prompts · {format_tokens(total.total)} total tokens "
        f"· {format_cost(total_cost)} estimated API cost{period_str}"
    )
    return 0


def _prepare_tui_terminal() -> None:
    """Read TUI keyboard input from the controlling terminal when possible."""
    if os.name != "posix" or sys.stdin.isatty():
        return

    try:
        tty_fd = os.open("/dev/tty", os.O_RDONLY)
    except OSError:
        return

    try:
        if tty_fd != 0:
            os.dup2(tty_fd, 0)
    finally:
        if tty_fd != 0:
            os.close(tty_fd)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    since = _parse_date(args.since)
    until = _parse_date(args.until)
    if since is None and args.days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
    # If --until is a date-only string, extend to end of day
    if args.until and len(args.until) == 10 and until is not None:
        until = until + timedelta(days=1) - timedelta(microseconds=1)

    if args.list:
        return _print_list(args.dir, args.project, args.limit, args.group, since, until)

    _prepare_tui_terminal()

    # Imported lazily so --list / --help work without textual installed.
    from .app import run

    run(
        args.dir,
        project_filter=args.project,
        limit=args.limit,
        grouped=args.group,
        since=since,
        until=until,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
