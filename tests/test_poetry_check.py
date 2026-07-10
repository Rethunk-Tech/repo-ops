"""Tests for check_poetry (#70 — must pass TIMEOUT_CHECK to run)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_models import Workspace
from repo_ops_py_poetry.poetry import check_poetry


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="poetry",
        git_repo=tmp_path,
        label="demo",
    )


def test_check_poetry_passes_timeout_check(tmp_path: Path) -> None:
    """#70 — poetry update --dry-run must be called with timeout=TIMEOUT_CHECK."""
    captured_timeout: list[int | None] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        captured_timeout.append(timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
    ):
        check_poetry(_ws(tmp_path))

    assert captured_timeout == [TIMEOUT_CHECK]


def test_check_poetry_tool_absent_warns_and_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """poetry not on PATH -> [] plus a WARN line on stderr, no subprocess call."""
    with patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None):
        changes = check_poetry(_ws(tmp_path))

    assert changes == []
    assert "poetry not on PATH" in capsys.readouterr().err


def test_check_poetry_parses_updating_lines(tmp_path: Path) -> None:
    """Canned 'poetry update --dry-run' output parses into Change entries."""
    stdout = (
        "Resolving dependencies...\n"
        "Package operations: 1 update\n"
        "  - Updating requests (2.28.0 -> 2.31.0)\n"
    )

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    with (
        patch(
            "repo_ops_py_poetry.poetry.shutil.which",
            return_value=sys.executable,
        ),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
    ):
        changes = check_poetry(_ws(tmp_path))

    assert len(changes) == 1
    assert changes[0].name == "requests"
    assert changes[0].from_ver == "2.28.0"
    assert changes[0].to == "2.31.0"
