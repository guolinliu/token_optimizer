# claude-gists

A terminal UI for browsing **recent Claude prompts, token consumption, and
estimated API cost** from your local Claude Code history.

It reads the session transcripts Claude Code writes to
`~/.claude/projects/<project>/*.jsonl`, pairs each typed prompt with the token
usage of the response it triggered, and presents a scrollable, drill-in table.

```
┌ Claude Prompt Gists ───────────────────────────────────────────────────────────┐
│ Time         Project      Tokens  Cost   In     Out    CacheW  CacheR  Model     Gist │
│ 06-26 14:02  token_optim  21.7k   $0.19  605    22.5k  3.1k    820.9k  opus-4-8  Scaf…│
│ 06-26 13:40  tbench       8.2k    $0.03  1.2k   3.4k   0       18.2k   sonnet…   Fix …│
└─────────────────────────────────────────────────────────────────────────────────┘
```

Columns: **Tokens** (grand total) · **In** (input) · **Out** (output) ·
**Cost** (estimated Claude API cost) · **CacheW** (cache-creation) ·
**CacheR** (cache-read).

## Install / run

This project uses [uv](https://docs.astral.sh/uv/).

```bash
uv run claude-gists            # launch the TUI
uv run claude-gists --group    # start grouped by project (toggle with 'g')
uv run claude-gists --list     # plain-text dump (no TUI)
uv run claude-gists --limit 50 --project tbench
```

Or install the console script:

```bash
uv pip install -e .
claude-gists
```

## Options

| Flag | Description |
|------|-------------|
| `--limit N` | Max recent prompts to show (default 200). |
| `--project SUBSTR` | Filter to projects whose label contains `SUBSTR`. |
| `--group` | Start grouped by project (also works with `--list`). |
| `--dir PATH` | Override the transcripts dir (default `~/.claude/projects`). |
| `--list` | Print a plain table instead of launching the TUI. |

Set `CLAUDE_CONFIG_DIR` to point at a non-default Claude config location.

## User guide

By default, `claude-gists` shows the latest 200 prompts from local Claude Code
history. Grouped view groups only those loaded prompts, so projects outside the
current limit will not appear. Increase the limit when you want a fuller
project-level cost view:

```bash
uv run claude-gists --group --limit 100000
```

Filter to projects whose decoded path contains a keyword with `--project`:

```bash
uv run claude-gists --project tbench
uv run claude-gists --group --project game-1024
uv run claude-gists --group --limit 100000 --project token
```

Use `--list` for a quick terminal report instead of the interactive TUI:

```bash
uv run claude-gists --list --group --limit 100000
```

Inside the TUI, press `g` to switch between flat and grouped views. In grouped
view, `space`/`enter` folds or unfolds the highlighted project, `f` folds all
projects, and `z` toggles fold all / unfold all.

`make run` launches the default TUI (`uv run claude-gists`). It does not
currently pass extra CLI arguments, so use `uv run claude-gists ...` directly
when you need `--group`, `--limit`, `--project`, or `--list`.

## TUI keys

| Key | Action |
|-----|--------|
| `↑`/`↓` or `j`/`k` | Move selection |
| `g` | Toggle grouping by project |
| `space` or `enter` | Fold/unfold the project at the cursor (grouped view) |
| `f` | Fold all projects |
| `z` | Fold all projects / unfold all |
| `r` | Reload from disk |
| `q` | Quit |

In grouped view, each project shows a header row (`▼` expanded / `▶` folded)
with its total tokens and prompt count; prompts are listed (indented)
underneath. Fold a project to hide its prompts and keep just the header — handy
for scanning token totals across many projects. Highlighting a project header
shows that project's aggregate token breakdown, prompt count, average
tokens/prompt, and active time range in the detail pane.

The bottom pane shows the full prompt text and a token breakdown
(input / output / cache-write / cache-read) plus estimated API cost for the
highlighted row.

## How tokens are attributed

Each line of a session `.jsonl` is a JSON event. A typed prompt is a
`type: "user"` event whose `message.content` is human text (tool results and
sidechain/sub-agent turns are skipped). Every `type: "assistant"` event that
follows — until the next typed prompt — contributes its `message.usage` block,
which is summed into that prompt's cost.

## Cost estimation

Cost is estimated from the public Claude API pricing at
<https://claude.com/pricing#api>. The formula uses model-specific rates for
uncached input, output, cache-write, and cache-read tokens:

```text
cost =
  input_tokens * input_rate
+ output_tokens * output_rate
+ cache_creation_input_tokens * cache_write_5min_rate
+ cache_read_input_tokens * cache_read_rate
```

Rates are USD per million tokens. Claude Code local history records cache
creation tokens but not the cache TTL, so cache writes are estimated with the
5-minute prompt-cache write rate. Unknown model IDs display `—` for cost.

## Distribution

The project ships three ways, covering both Python and non-Python users.

### 1. PyPI — for anyone with Python (recommended)

Once published, users run it with zero setup via `uvx`, or install it isolated
with `pipx`:

```bash
uvx claude-gists              # run without a persistent install
pipx install claude-gists     # install on PATH in its own venv
```

Maintainer steps (PyPI Trusted Publishing is wired up in CI):

```bash
make dist            # uv build -> dist/*.whl + *.tar.gz
make publish-test    # upload to TestPyPI
make publish         # upload to PyPI
```

### 2. Standalone binary — for users without Python

A single self-contained executable (no Python/pip needed on the target):

```bash
make binary          # -> dist/claude-gists  (~13MB)
./dist/claude-gists
```

Built with PyInstaller via `claude-gists.spec`.

### 3. GitHub Releases — automated

Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yml`, which publishes to
PyPI and attaches Linux/macOS/Windows binaries to the GitHub Release:

```bash
git tag v0.1.0 && git push --tags
```

## Development

```bash
make install   # uv sync --extra dev --extra build
make test      # uv run --extra dev pytest
make run       # launch the TUI from source
make help      # list all targets
```

### Testing presentation and Textual

The app uses a small MVVM split so display data can be tested without starting
the terminal UI.

Use `claude_gists.viewmodel.GistsViewModel` for presentation tests. It accepts
ordinary `PromptGist` objects and returns inspectable row/detail dataclasses:

```python
from claude_gists.viewmodel import GistsViewModel

view_model = GistsViewModel(gists, grouped=True, collapsed={"sample-project"})
rows = view_model.table_rows()
detail = view_model.detail_for_row(0)

assert rows[0].kind == "header"
assert rows[0].tokens == "880"
assert detail.project == "sample-project"
```

This is the preferred place to assert exact formatted labels, token strings,
group headers, collapsed state, and detail payloads.

Use Textual's headless test harness for UI behavior. `App.run_test()` runs the
TUI without taking over the terminal and provides a `pilot` for key presses:

```python
from textual.widgets import DataTable

from claude_gists.app import GistsApp

app = GistsApp(projects_dir=fixtures)
async with app.run_test() as pilot:
    await pilot.pause()
    table = app.query_one("#table", DataTable)
    assert table.row_count == 3

    await pilot.press("g")
    await pilot.pause()
    assert table.row_count == 4
```

Use Textual tests for integration checks: key bindings, folding behavior,
widget updates, cursor/highlight behavior, and render regressions. Avoid using
them for every exact string when a ViewModel test can inspect the same
presentation data more directly.

Layout:

```
claude_gists/
  models.py    # PromptGist, TokenUsage, formatting helpers
  history.py   # transcript discovery + parsing
  pricing.py   # Claude API token cost estimation
  viewmodel.py # display-ready rows/details, independent of Textual
  app.py       # Textual TUI
  cli.py       # argparse entry point (claude-gists)
tests/         # parser, ViewModel, and Textual tests + fixture transcript
```
