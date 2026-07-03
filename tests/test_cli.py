import pytest

from claude_gists import cli


class FakeStdin:
    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_prepare_tui_terminal_keeps_tty_stdin(monkeypatch):
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(is_tty=True))
    monkeypatch.setattr(
        cli.os,
        "open",
        lambda *_args, **_kwargs: pytest.fail("should not open /dev/tty"),
    )

    cli._prepare_tui_terminal()


def test_prepare_tui_terminal_redirects_non_tty_stdin(monkeypatch):
    calls = []

    def fake_open(path, flags):
        calls.append(("open", path, flags))
        return 9

    def fake_dup2(src, dst):
        calls.append(("dup2", src, dst))

    def fake_close(fd):
        calls.append(("close", fd))

    monkeypatch.setattr(cli.os, "name", "posix")
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(is_tty=False))
    monkeypatch.setattr(cli.os, "open", fake_open)
    monkeypatch.setattr(cli.os, "dup2", fake_dup2)
    monkeypatch.setattr(cli.os, "close", fake_close)

    cli._prepare_tui_terminal()

    assert calls == [
        ("open", "/dev/tty", cli.os.O_RDONLY),
        ("dup2", 9, 0),
        ("close", 9),
    ]


def test_prepare_tui_terminal_ignores_missing_controlling_terminal(monkeypatch):
    def fake_open(_path, _flags):
        raise OSError()

    monkeypatch.setattr(cli.os, "name", "posix")
    monkeypatch.setattr(cli.sys, "stdin", FakeStdin(is_tty=False))
    monkeypatch.setattr(cli.os, "open", fake_open)

    cli._prepare_tui_terminal()


def test_list_mode_does_not_prepare_tui_terminal(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli,
        "_prepare_tui_terminal",
        lambda: pytest.fail("list mode should not prepare the TUI terminal"),
    )

    assert cli.main(["--list", "--dir", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "No prompts found" in output
