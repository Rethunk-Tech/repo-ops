"""Tests for check_poetry."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_py_poetry.poetry import check_poetry


def test_check_poetry_passes_timeout_check(tmp_path: Path, make_workspace) -> None:
    """Poetry update --dry-run must be called with timeout=TIMEOUT_CHECK."""
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
        check_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    assert captured_timeout == [TIMEOUT_CHECK]


def test_check_poetry_raises_on_nonzero_dry_run(tmp_path: Path, make_workspace) -> None:
    """Non-zero poetry update --dry-run raises RuntimeError."""

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="solver failed")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        pytest.raises(RuntimeError, match="solver failed"),
    ):
        check_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))


def test_apply_poetry_runs_poetry_update(tmp_path: Path, make_workspace, fake_run: Any) -> None:
    """apply_poetry success path invokes poetry update."""
    from repo_ops_py_poetry.poetry import apply_poetry

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
    ):
        apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    assert fake_run.calls == [["poetry", "update"]]


def test_apply_poetry_absent_raises_without_warn_prefix(tmp_path: Path, make_workspace) -> None:
    """apply_poetry missing poetry raises plain error (no WARN prefix)."""
    from repo_ops_py_poetry.poetry import apply_poetry

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match=r"^poetry not on PATH"),
    ):
        apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))
