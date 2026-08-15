"""Integration test: uv lock dry-run via check_uv_lock.

Constructs a minimal Hatch-style ``pyproject.toml`` with an old dependency pin,
materializes ``uv.lock`` via ``uv lock``, then runs ``check_uv_lock``.

Network: ``uv lock`` may reach PyPI on first run; skipped on network failure.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap

import pytest

from repo_ops_models import Workspace
from repo_ops_py.uv_lock import check_uv_lock

pytestmark = pytest.mark.integration

_UV = shutil.which("uv")


@pytest.mark.skipif(_UV is None, reason="uv not on PATH")
def test_check_uv_lock_runs_dry_run(tmp_path) -> None:
    """check_uv_lock completes against a real uv.lock without crashing."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [project]
            name = "uv-integ-test"
            version = "0.1.0"
            requires-python = ">=3.10"
            dependencies = ["requests==2.28.0"]
        """),
        encoding="utf-8",
    )

    try:
        proc = subprocess.run(
            [_UV, "lock"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("uv lock timed out — network unavailable")

    if proc.returncode != 0:
        combined = (proc.stdout + proc.stderr).lower()
        if any(
            kw in combined for kw in ("dial", "connect", "network", "timeout", "i/o")
        ):
            pytest.skip(f"uv lock failed (network): {proc.stderr[:200]}")
        pytest.fail(f"uv lock failed: {proc.stderr[:400]}")

    ws = Workspace(
        path=tmp_path,
        ecosystem="python",
        manager="uv",
        git_repo=tmp_path,
        label="uv-integ",
    )
    changes = check_uv_lock(ws)
    assert isinstance(changes, list)
