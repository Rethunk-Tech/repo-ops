"""Coverage for repo_ops_py.uv_lock check parsing and failure raise."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_models import Workspace
from repo_ops_py import uv_lock


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path, ecosystem="py", manager="uv", git_repo=tmp_path, label="demo"
    )


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    def _run(cmd, cwd, timeout=None, env=None):
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr
        )

    return _run


def test_check_uv_lock_parses_would_upgrade_line(tmp_path: Path) -> None:
    stdout = "Resolved 10 packages\nwould upgrade foo 1.0 -> 2.0\n"
    with patch.object(uv_lock, "run", _fake_run(stdout=stdout)):
        changes = uv_lock.check_uv_lock(_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].name == "foo"
    assert changes[0].from_ver == "1.0"
    assert changes[0].to == "2.0"


def test_check_uv_lock_falls_back_to_lockfile_only_change(tmp_path: Path) -> None:
    """No parseable 'name A -> B' line, but 'would change' present -> single
    synthetic (lockfile) Change."""
    stdout = "Resolved 10 packages\nuv.lock would change\n"
    with patch.object(uv_lock, "run", _fake_run(stdout=stdout)):
        changes = uv_lock.check_uv_lock(_ws(tmp_path))
    assert len(changes) == 1
    assert changes[0].name == "(lockfile)"
    assert changes[0].from_ver == "current"
    assert changes[0].to == "upgraded"


def test_check_uv_lock_raises_runtime_error_on_nonzero_exit(tmp_path: Path) -> None:
    with (
        patch.object(
            uv_lock, "run", _fake_run(stderr="resolution failed", returncode=1)
        ),
        pytest.raises(RuntimeError, match="resolution failed"),
    ):
        uv_lock.check_uv_lock(_ws(tmp_path))
