"""Tests for pipenv check/apply."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

from repo_ops_py_pipenv.pipenv import apply_pipenv, check_pipenv


def test_check_pipenv_raises_when_pip_fails(tmp_path: Path, make_workspace) -> None:
    """When the venv probe succeeds but pip list fails, check_pipenv raises RuntimeError."""
    call_count = 0

    def fake_run(cmd, cwd, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Probe succeeds — venv found at /fake/.venv
            return subprocess.CompletedProcess(cmd, 0, stdout="/fake/.venv", stderr="")
        # pip list fails
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pip failed")

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        try:
            check_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))
        except RuntimeError as exc:
            assert "pip failed" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_check_pipenv_invokes_venv_probe_then_pip(
    tmp_path: Path, make_workspace, fake_run: Any
) -> None:
    """check_pipenv probes for the venv first, then calls pip list --outdated --format=json."""

    with (
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch("repo_ops_py_pipenv.pipenv.run", side_effect=fake_run),
    ):
        check_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))

    assert len(fake_run.calls) == 2, f"expected 2 calls (probe + pip), got {fake_run.calls}"
    assert fake_run.calls[0] == ["pipenv", "--venv"], (
        f"first call must be probe; got {fake_run.calls[0]}"
    )
    pip_call = fake_run.calls[1]
    assert "pip" in pip_call[0], f"second call must invoke pip; got {pip_call}"
    assert "--outdated" in pip_call
    assert "--format=json" in pip_call


def test_apply_pipenv_raises_when_pipenv_absent(tmp_path: Path, make_workspace) -> None:
    with patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value=None):
        try:
            apply_pipenv(make_workspace(tmp_path, ecosystem="py", manager="pipenv", label="demo"))
        except RuntimeError as exc:
            assert "pipenv not on PATH" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
