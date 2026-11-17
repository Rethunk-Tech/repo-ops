"""Tests for check_poetry."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_models import Change
from repo_ops_py_poetry.poetry import (
    _apply_poetry_targeted_bumps,
    _poetry_bumped_requirement,
    _poetry_direct_dependencies,
    apply_poetry,
    check_poetry,
)


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
    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match=r"^poetry not on PATH"),
    ):
        apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))


def test_poetry_direct_dependencies_skips_python_vcs_and_reads_groups(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        """\
[tool.poetry.dependencies]
python = "^3.12"
requests = "^2.31"
gitdep = { git = "https://example.com/x.git" }
flask = { version = "^3.0" }
loose = { extras = ["foo"] }

[tool.poetry.dev-dependencies]
pytest = "^8.0"

[tool.poetry.group.lint.dependencies]
ruff = "^0.8"
""",
        encoding="utf-8",
    )
    assert _poetry_direct_dependencies(manifest) == {
        "requests": "^2.31",
        "flask": "^3.0",
        "pytest": "^8.0",
        "ruff": "^0.8",
    }


def test_poetry_bumped_requirement_tilde_and_unprefixed() -> None:
    assert _poetry_bumped_requirement("~1.2", "1.3.0") == "~1.3.0"
    assert _poetry_bumped_requirement(">=1.0", "2.0.0") == "2.0.0"


def test_check_poetry_missing_on_path_returns_empty(
    tmp_path: Path, make_workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    result = None
    with patch("repo_ops_py_poetry.poetry.shutil.which", return_value=None):
        result = check_poetry(
            make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo")
        )
    assert result == ([], [])
    assert "WARN" in capsys.readouterr().err


def test_check_poetry_parses_updating_and_upgrading_lines(tmp_path: Path, make_workspace) -> None:
    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Updating requests (2.31.0 -> 2.32.0)\n",
            stderr="Upgrading flask (3.0.0 -> 3.1.0)\n",
        )

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        patch("repo_ops_py_poetry.poetry._pypi_latest", return_value=None),
    ):
        in_range, latest = check_poetry(
            make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo")
        )

    assert in_range == [
        Change("requests", "2.31.0", "2.32.0"),
        Change("flask", "3.0.0", "3.1.0"),
    ]
    assert latest == []


def test_apply_poetry_raises_on_nonzero_update(tmp_path: Path, make_workspace) -> None:
    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", fake_run),
        pytest.raises(RuntimeError, match="poetry update failed"),
    ):
        apply_poetry(make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo"))


def _poetry_ws_with_requests(tmp_path: Path, make_workspace):
    ws = make_workspace(tmp_path, ecosystem="py", manager="poetry", label="demo")
    (ws.path / "pyproject.toml").write_text(
        """\
[tool.poetry.dependencies]
python = "^3.12"
requests = "^2.0"
""",
        encoding="utf-8",
    )
    (ws.path / "poetry.lock").write_text("", encoding="utf-8")
    return ws


def test_apply_poetry_targeted_bumps_skips_unknown_and_held(tmp_path: Path, make_workspace) -> None:
    ws = _poetry_ws_with_requests(tmp_path, make_workspace)
    with patch("repo_ops_py_poetry.poetry.target_within_hold", return_value=False):
        remaining = _apply_poetry_targeted_bumps(
            ws,
            [
                Change("unknown", "1.0", "2.0"),
                Change("requests", "2.0.0", "2.32.0"),
            ],
        )
    assert [(c.name, c.to) for c in remaining] == [
        ("unknown", "2.0"),
        ("requests", "2.32.0"),
    ]


def test_apply_poetry_targeted_bumps_restores_on_add_error(tmp_path: Path, make_workspace) -> None:
    ws = _poetry_ws_with_requests(tmp_path, make_workspace)
    manifest = ws.path / "pyproject.toml"
    original = manifest.read_text(encoding="utf-8")

    def boom(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["poetry", "add"]:
            manifest.write_text("corrupted", encoding="utf-8")
            raise RuntimeError("add failed")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch("repo_ops_py_poetry.poetry.run", boom),
        pytest.raises(RuntimeError, match="add failed"),
    ):
        _apply_poetry_targeted_bumps(ws, [Change("requests", "2.0.0", "2.32.0")])
    assert manifest.read_text(encoding="utf-8") == original
