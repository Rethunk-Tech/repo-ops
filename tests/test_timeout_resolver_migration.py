"""#118 — env-overridable timeout resolvers apply to all migrated plugins.

Verifies that REPO_OPS_TIMEOUT_CHECK and REPO_OPS_TIMEOUT_APPLY are respected
at call-time in the migrated check/apply functions — not frozen at import time.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_models import Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY, TIMEOUT_CHECK


def _ws(tmp_path: Path, ecosystem: str, manager: str) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem=ecosystem,
        manager=manager,
        git_repo=tmp_path,
        label="demo",
    )


def _fake_run(captured: list[dict]):
    """Return a side-effect that records (cmd, timeout) and returns a zero proc."""

    def _run(cmd, cwd, timeout=None, **_kwargs):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    return _run


# ---------------------------------------------------------------------------
# REPO_OPS_TIMEOUT_CHECK env override reaches a migrated checker
# ---------------------------------------------------------------------------


def test_timeout_check_env_override_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_CHECK overrides the timeout passed to run() in check_uv."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", "42")
    captured: list[dict] = []

    from repo_ops_py_uv.uv import check_uv

    with patch("repo_ops_py.uv_lock.run", side_effect=_fake_run(captured)):
        check_uv(_ws(tmp_path, "py", "uv"))

    assert captured, "run() was never called"
    assert captured[0]["timeout"] == 42, (
        f"expected timeout=42 (from env), got {captured[0]['timeout']}"
    )


def test_timeout_check_env_override_rust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_CHECK overrides the timeout passed to run() in check_rust."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", "77")
    captured: list[dict] = []

    from repo_ops_rust.rust import check_rust

    with patch("repo_ops_rust.rust.run", side_effect=_fake_run(captured)):
        check_rust(_ws(tmp_path, "rust", "cargo"))

    assert captured, "run() was never called"
    assert captured[0]["timeout"] == 77, (
        f"expected timeout=77 (from env), got {captured[0]['timeout']}"
    )


def test_timeout_check_env_override_poetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_CHECK overrides the timeout passed to run() in check_poetry."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", "55")
    captured: list[dict] = []

    from repo_ops_py_poetry.poetry import check_poetry

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", side_effect=_fake_run(captured)),
    ):
        check_poetry(_ws(tmp_path, "py", "poetry"))

    assert captured, "run() was never called"
    assert captured[0]["timeout"] == 55


def test_timeout_check_env_override_bun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_CHECK overrides the timeout passed to run() in check_bun."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", "33")
    captured: list[dict] = []

    from repo_ops_js_bun.bun import check_bun

    with patch("repo_ops_js_bun.bun.run", side_effect=_fake_run(captured)):
        check_bun(_ws(tmp_path, "js", "bun"))

    assert captured, "run() was never called"
    assert captured[0]["timeout"] == 33


# ---------------------------------------------------------------------------
# REPO_OPS_TIMEOUT_APPLY env override reaches migrated appliers
# ---------------------------------------------------------------------------


def test_timeout_apply_env_override_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_APPLY overrides the timeout passed to run() in apply_uv."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_APPLY", "99")
    captured: list[dict] = []

    from repo_ops_py_uv.uv import apply_uv

    with patch("repo_ops_py.uv_lock.run", side_effect=_fake_run(captured)):
        apply_uv(_ws(tmp_path, "py", "uv"))

    assert captured, "run() was never called"
    assert all(c["timeout"] == 99 for c in captured), (
        f"expected all timeouts=99, got {[c['timeout'] for c in captured]}"
    )


def test_timeout_apply_env_override_rust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_APPLY overrides the timeout passed to run() in apply_rust."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_APPLY", "88")
    captured: list[dict] = []

    from repo_ops_rust.rust import apply_rust

    with patch("repo_ops_rust.rust.run", side_effect=_fake_run(captured)):
        apply_rust(_ws(tmp_path, "rust", "cargo"))

    assert captured, "run() was never called"
    assert all(c["timeout"] == 88 for c in captured)


def test_timeout_apply_env_override_poetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REPO_OPS_TIMEOUT_APPLY overrides the timeout passed to run() in apply_poetry."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_APPLY", "66")
    captured: list[dict] = []

    from repo_ops_py_poetry.poetry import apply_poetry

    with (
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch("repo_ops_py_poetry.poetry.run", side_effect=_fake_run(captured)),
    ):
        apply_poetry(_ws(tmp_path, "py", "poetry"))

    assert captured, "run() was never called"
    assert all(c["timeout"] == 66 for c in captured)


# ---------------------------------------------------------------------------
# Default (no env set) still uses the original constant values
# ---------------------------------------------------------------------------


def test_timeout_check_default_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env override, check functions use TIMEOUT_CHECK."""
    monkeypatch.delenv("REPO_OPS_TIMEOUT_CHECK", raising=False)
    captured: list[dict] = []

    from repo_ops_py_uv.uv import check_uv

    with patch("repo_ops_py.uv_lock.run", side_effect=_fake_run(captured)):
        check_uv(_ws(tmp_path, "py", "uv"))

    assert captured[0]["timeout"] == TIMEOUT_CHECK


def test_timeout_apply_default_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without env override, apply functions use TIMEOUT_APPLY."""
    monkeypatch.delenv("REPO_OPS_TIMEOUT_APPLY", raising=False)
    captured: list[dict] = []

    from repo_ops_rust.rust import apply_rust

    with patch("repo_ops_rust.rust.run", side_effect=_fake_run(captured)):
        apply_rust(_ws(tmp_path, "rust", "cargo"))

    assert captured[0]["timeout"] == TIMEOUT_APPLY
