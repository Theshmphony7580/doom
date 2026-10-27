import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from pi_in_py.app.branding import startup_banner
from pi_in_py.app.cli import run


def test_banner_preserves_the_cursor_and_fits_terminal_width() -> None:
    for width in (12, 20, 43, 44, 80):
        banner = startup_banner(width)
        assert "doom" in banner
        assert all(len(line) <= width for line in banner.splitlines())
        assert "*" not in banner
        assert "\x1b" not in banner
    assert len(startup_banner().splitlines()) == 7
    assert startup_banner(color=True).count("\x1b[38;2;240;113;120m▄") == 1
    assert startup_banner(unicode=False) == "doom - Workspace assistant"


def test_help_uses_doom_as_the_command_name() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pi_in_py", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: doom" in result.stdout
    assert "Doom - your workspace assistant" in result.stdout


@pytest.mark.parametrize("is_terminal", [False, True])
def test_banner_uses_stderr_and_keeps_demo_output_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    is_terminal: bool,
) -> None:
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: is_terminal)
    (tmp_path / "README.md").write_text("Offline input", encoding="utf-8")
    args = argparse.Namespace(
        provider="anthropic",
        model=None,
        demo=True,
        workspace=str(tmp_path),
        session=str(tmp_path / "demo.jsonl"),
        max_turns=2,
        trace=False,
        prompt=None,
    )
    assert asyncio.run(run(args)) == 0
    output = capsys.readouterr()
    assert "Offline demo finished" in output.out
    assert "doom" not in output.out
    assert "█" not in output.out
    assert ("Workspace assistant" in output.err) == is_terminal
    assert "\x1b" not in output.err
