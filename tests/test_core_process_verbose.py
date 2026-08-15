"""Coverage for repo_ops_core.process verbose echoes and the bad-cwd error path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from repo_ops_core import process


def test_run_nonexistent_cwd_raises_file_not_found_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError, match="cwd is not a directory"):
        process.run(["true"], missing)


def test_run_verbose_echoes_command_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """VERBOSE=True echoes the command line, and a nonzero exit with output
    also echoes the '! exit <code>' tail to stderr."""
    process.set_verbose(True)
    try:
        proc = process.run(
            [
                sys.executable,
                "-c",
                "import sys; print('boom', file=sys.stderr); sys.exit(3)",
            ],
            tmp_path,
        )
    finally:
        process.set_verbose(False)

    assert proc.returncode == 3
    captured = capsys.readouterr()
    assert f"  $ {sys.executable} -c" in captured.err
    assert "! exit 3:" in captured.err
    assert "boom" in captured.err


def test_run_verbose_success_only_echoes_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A successful command under VERBOSE only echoes the command line, not
    the '! exit' failure tail."""
    process.set_verbose(True)
    try:
        proc = process.run([sys.executable, "-c", "pass"], tmp_path)
    finally:
        process.set_verbose(False)

    assert proc.returncode == 0
    captured = capsys.readouterr()
    assert "  $ " in captured.err
    assert "! exit" not in captured.err
