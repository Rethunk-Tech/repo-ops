"""Tests for PDM check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_py_pdm.pdm import apply_pdm, check_pdm


def test_check_pdm_raises_on_nonzero_returncode(tmp_path: Path, make_workspace) -> None:
    def fake_run(cmd, cwd, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="lock file is corrupt")

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        try:
            check_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))
        except RuntimeError as exc:
            assert "lock file is corrupt" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_apply_pdm_runs_pdm_update(tmp_path: Path, make_workspace) -> None:
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        apply_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))

    assert captured == [["pdm", "update"]]


def test_apply_pdm_raises_when_pdm_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_py_pdm.pdm.shutil.which", return_value=None):
        try:
            apply_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))
        except RuntimeError as exc:
            assert "pdm not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
