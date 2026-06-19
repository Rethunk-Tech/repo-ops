"""Tests for check_poetry (#70 — must pass TIMEOUT_CHECK to run)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

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
