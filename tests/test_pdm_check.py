"""Tests for PDM check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
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


def test_apply_pdm_runs_pdm_update(tmp_path: Path, make_workspace, fake_run: Any) -> None:

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
    ):
        apply_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))

    assert fake_run.calls == [["pdm", "update"]]


def test_apply_pdm_raises_when_pdm_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_py_pdm.pdm.shutil.which", return_value=None):
        try:
            apply_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="demo"))
        except RuntimeError as exc:
            assert "pdm not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


_LOCK = '[[package]]\nname = "packaging"\nversion = "{v}"\n'


def test_check_pdm_diffs_a_scratch_lock_and_leaves_the_checkout_alone(
    tmp_path: Path, make_workspace
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "pdm.lock").write_text(_LOCK.format(v="21.3"))

    def fake_run(cmd, cwd, timeout=None):
        assert Path(cwd) != tmp_path
        (Path(cwd) / "pdm.lock").write_text(_LOCK.format(v="26.3"))
        (Path(cwd) / ".pdm-python").write_text("x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch("repo_ops_py_pdm.pdm.run", side_effect=fake_run),
        patch("repo_ops_py_pdm.pdm._pdm_latest_direct_changes", return_value=[]),
    ):
        changes, _ = check_pdm(make_workspace(tmp_path, ecosystem="py", manager="pdm", label="d"))

    assert [(c.name, c.from_ver, c.to) for c in changes] == [("packaging", "21.3", "26.3")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pdm.lock", "pyproject.toml"]


def test_pdm_bump_of_a_range_stays_a_floor_not_an_exact_pin() -> None:
    from repo_ops_py.direct_requirements import bumped_requirement as bump

    assert bump(">=7.0,<8", "8.5.1") == ">=8.5.1"
    assert bump("==2.28.0", "2.31.0") == "==2.31.0"
    assert bump("^1.2", "2.0.0") == "^2.0.0"
    assert bump("~=1.2", "2.0.0") == "~=2.0.0"
