"""Tests for pipenv check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Workspace
from repo_ops_py.pipenv import apply_pipenv, check_pipenv


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pipenv",
        git_repo=tmp_path,
        label="demo",
    )


_OUTDATED_OUTPUT = """\
Package    Version  Latest  Type
---------- -------  ------  -----
requests   2.28.0   2.31.0  wheel
certifi    2022.12.7 2023.7.22 wheel
"""


def test_check_pipenv_parses_outdated_output(tmp_path: Path) -> None:
    """check_pipenv must parse the tabular output of pip list --outdated."""
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=_OUTDATED_OUTPUT, stderr="")

    with (
        patch("repo_ops_py.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    assert len(changes) == 2
    names = {c.name for c in changes}
    assert "requests" in names
    assert "certifi" in names


def test_check_pipenv_returns_empty_when_up_to_date(tmp_path: Path) -> None:
    empty_output = "Package  Version  Latest  Type\n------- -------- ------- -----\n"

    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=empty_output, stderr="")

    with (
        patch("repo_ops_py.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py.pipenv.run", side_effect=fake_run),
    ):
        changes = check_pipenv(_ws(tmp_path))

    assert changes == []


def test_check_pipenv_warns_when_pipenv_absent(tmp_path: Path, capsys) -> None:
    """check_pipenv must not raise when pipenv is absent — returns [] + WARN."""
    with patch("repo_ops_py.pipenv.shutil.which", return_value=None):
        result = check_pipenv(_ws(tmp_path))

    assert result == []
    assert "WARN" in capsys.readouterr().err


def test_check_pipenv_raises_on_nonzero_returncode(tmp_path: Path) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="virtual env not found")

    with (
        patch("repo_ops_py.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py.pipenv.run", side_effect=fake_run),
    ):
        try:
            check_pipenv(_ws(tmp_path))
        except RuntimeError as exc:
            assert "virtual env not found" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_check_pipenv_invokes_correct_command(tmp_path: Path) -> None:
    """check_pipenv must call 'pipenv run pip list --outdated --format=columns'."""
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="Package Version Latest Type\n---\n", stderr="")

    with (
        patch("repo_ops_py.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py.pipenv.run", side_effect=fake_run),
    ):
        check_pipenv(_ws(tmp_path))

    assert captured == [["pipenv", "run", "pip", "list", "--outdated", "--format=columns"]]


def test_apply_pipenv_runs_pipenv_update(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py.pipenv.run", side_effect=fake_run),
    ):
        apply_pipenv(_ws(tmp_path))

    assert captured == [["pipenv", "update"]]


def test_apply_pipenv_raises_when_pipenv_absent(tmp_path: Path) -> None:
    with patch("repo_ops_py.pipenv.shutil.which", return_value=None):
        try:
            apply_pipenv(_ws(tmp_path))
        except RuntimeError as exc:
            assert "pipenv not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
