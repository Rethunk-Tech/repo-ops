"""Tests for check_poetry."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
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


def test_check_poetry_tool_absent_warns_and_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch, make_workspace
) -> None:
    """poetry not on PATH -> [] plus a WARN line on stderr, no subprocess call."""
    monkeypatch.setenv("REPO_OPS_POETRY_TARGETED_BUMP", "0")
    with patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None):
        changes = check_poetry(
            make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo")
        )

    assert changes == []
    assert "poetry not on PATH" in capsys.readouterr().err


def test_check_poetry_parses_updating_lines(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """Canned 'poetry update --dry-run' output parses into Change entries."""
    monkeypatch.setenv("REPO_OPS_POETRY_TARGETED_BUMP", "0")
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
        changes = check_poetry(
            make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo")
        )

    assert len(changes) == 1
    assert changes[0].name == "requests"
    assert changes[0].from_ver == "2.28.0"
    assert changes[0].to == "2.31.0"


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


def test_check_poetry_parses_upgrading_stderr_lines(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Upgrading lines on stderr are parsed into Change entries."""
    monkeypatch.setenv("REPO_OPS_POETRY_TARGETED_BUMP", "0")
    stderr = "  - Upgrading certifi (2022.12.7 -> 2023.7.22)\n"

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=stderr)

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
    ):
        changes = check_poetry(
            make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo")
        )

    assert len(changes) == 1
    assert changes[0].name == "certifi"
    assert changes[0].from_ver == "2022.12.7"
    assert changes[0].to == "2023.7.22"


def test_apply_poetry_runs_poetry_update(tmp_path: Path, make_workspace) -> None:
    """apply_poetry success path invokes poetry update."""
    from repo_ops_py_poetry.poetry import apply_poetry

    captured: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
    ):
        apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))

    assert captured == [["poetry", "update"]]


def test_apply_poetry_absent_raises_without_warn_prefix(tmp_path: Path, make_workspace) -> None:
    """apply_poetry missing poetry raises plain error (no WARN prefix)."""
    from repo_ops_py_poetry.poetry import apply_poetry

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match=r"^poetry not on PATH"),
    ):
        apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))
