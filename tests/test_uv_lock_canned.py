"""Coverage for repo_ops_py.uv_lock check parsing and failure raise."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_py import uv_lock


def _fake_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    def _run(cmd, cwd, timeout=None, env=None):
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr
        )

    return _run


def test_check_uv_lock_parses_would_upgrade_line(tmp_path: Path, make_workspace) -> None:
    stdout = "Resolved 10 packages\nwould upgrade foo 1.0 -> 2.0\n"
    with patch.object(uv_lock, "run", _fake_run(stdout=stdout)):
        changes = uv_lock.check_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))
    assert len(changes) == 1
    assert changes[0].name == "foo"
    assert changes[0].from_ver == "1.0"
    assert changes[0].to == "2.0"


def test_check_uv_lock_falls_back_to_lockfile_only_change(tmp_path: Path, make_workspace) -> None:
    """No parseable 'name A -> B' line, but 'would change' present -> single
    synthetic (lockfile) Change."""
    stdout = "Resolved 10 packages\nuv.lock would change\n"
    with patch.object(uv_lock, "run", _fake_run(stdout=stdout)):
        changes = uv_lock.check_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))
    assert len(changes) == 1
    assert changes[0].name == "(lockfile)"
    assert changes[0].from_ver == "current"
    assert changes[0].to == "upgraded"


def test_check_uv_lock_raises_runtime_error_on_nonzero_exit(tmp_path: Path, make_workspace) -> None:
    with (
        patch.object(
            uv_lock, "run", _fake_run(stderr="resolution failed", returncode=1)
        ),
        pytest.raises(RuntimeError, match="resolution failed"),
    ):
        uv_lock.check_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))


def test_check_uv_lock_parses_prerelease_versions(tmp_path: Path, make_workspace) -> None:
    """F214 — pre-release / local version identifiers must parse."""
    stdout = (
        "would upgrade foo 1.0.0a1 -> 1.0.0b2\nwould upgrade bar 1.0.0+local -> 1.0.1\n"
    )
    with patch.object(uv_lock, "run", _fake_run(stdout=stdout)):
        changes = uv_lock.check_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))
    assert len(changes) == 2
    assert changes[0].from_ver == "1.0.0a1"
    assert changes[0].to == "1.0.0b2"
    assert changes[1].from_ver == "1.0.0+local"


def test_apply_uv_lock_runs_upgrade_then_sync(tmp_path: Path, make_workspace) -> None:
    """F153 — apply_uv_lock success path: uv lock --upgrade then uv sync."""
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None, env=None):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch.object(uv_lock, "run", fake_run):
        uv_lock.apply_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))

    assert calls == [["uv", "lock", "--upgrade"], ["uv", "sync"]]


def test_apply_uv_lock_raises_on_upgrade_failure(tmp_path: Path, make_workspace) -> None:
    """F153 — nonzero uv lock --upgrade raises RuntimeError."""
    with (
        patch.object(uv_lock, "run", _fake_run(stderr="upgrade failed", returncode=1)),
        pytest.raises(RuntimeError, match="upgrade failed"),
    ):
        uv_lock.apply_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))


def test_apply_uv_lock_raises_on_sync_failure(tmp_path: Path, make_workspace) -> None:
    """F153 — uv sync failure after successful lock upgrade raises."""
    call_n = 0

    def fake_run(cmd, cwd, timeout=None, env=None):
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="sync failed")

    with (
        patch.object(uv_lock, "run", fake_run),
        pytest.raises(RuntimeError, match="sync failed"),
    ):
        uv_lock.apply_uv_lock(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))
