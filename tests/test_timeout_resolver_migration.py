"""Env-overridable timeout resolvers apply to all migrated plugins.

Verifies that REPO_OPS_TIMEOUT_CHECK and REPO_OPS_TIMEOUT_APPLY are respected
at call-time in the migrated check/apply functions — not frozen at import time.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.constants import TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_js_bun.bun import check_bun
from repo_ops_py_poetry.poetry import apply_poetry, check_poetry
from repo_ops_py_uv.uv import apply_uv, check_uv
from repo_ops_rust.rust import apply_rust, check_rust


def _fake_run(captured: list[dict]):
    """Return a side-effect that records (cmd, timeout) and returns a zero proc."""

    def _run(cmd, cwd, timeout=None, **_kwargs):
        captured.append({"cmd": cmd, "timeout": timeout})
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    return _run


def _no_extra_patches(monkeypatch: pytest.MonkeyPatch) -> list:
    return []


def _rust_extra_patches(monkeypatch: pytest.MonkeyPatch) -> list:
    # Targeted bump would otherwise shell out beyond the single run() this
    # test observes.
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    return []


def _poetry_extra_patches(monkeypatch: pytest.MonkeyPatch) -> list:
    return [patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry")]


# ---------------------------------------------------------------------------
# REPO_OPS_TIMEOUT_CHECK env override reaches a migrated checker
# ---------------------------------------------------------------------------

_CHECK_CASES = [
    ("repo_ops_py.uv_lock.run", check_uv, "py", "uv", 42, _no_extra_patches),
    ("repo_ops_rust.rust.run", check_rust, "rust", "cargo", 77, _rust_extra_patches),
    (
        "repo_ops_py_poetry.poetry.run",
        check_poetry,
        "py",
        "poetry",
        55,
        _poetry_extra_patches,
    ),
    ("repo_ops_js_bun.bun.run", check_bun, "js", "bun", 33, _no_extra_patches),
]


@pytest.mark.parametrize(
    "patch_target,check_fn,ecosystem,manager,env_val,extra_patches",
    _CHECK_CASES,
    ids=[manager for _, _, _, manager, _, _ in _CHECK_CASES],
)
def test_timeout_check_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_workspace,
    patch_target,
    check_fn,
    ecosystem,
    manager,
    env_val,
    extra_patches,
) -> None:
    """REPO_OPS_TIMEOUT_CHECK overrides the timeout passed to run() in each checker."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_CHECK", str(env_val))
    captured: list[dict] = []

    with ExitStack() as stack:
        for extra in extra_patches(monkeypatch):
            stack.enter_context(extra)
        stack.enter_context(patch(patch_target, side_effect=_fake_run(captured)))
        check_fn(make_workspace(tmp_path, ecosystem=ecosystem, manager=manager, label="demo"))

    assert captured, "run() was never called"
    assert captured[0]["timeout"] == env_val, (
        f"expected timeout={env_val} (from env), got {captured[0]['timeout']}"
    )


# ---------------------------------------------------------------------------
# REPO_OPS_TIMEOUT_APPLY env override reaches migrated appliers
# ---------------------------------------------------------------------------

_APPLY_CASES = [
    ("repo_ops_py.uv_lock.run", apply_uv, "py", "uv", 99, _no_extra_patches),
    ("repo_ops_rust.rust.run", apply_rust, "rust", "cargo", 88, _rust_extra_patches),
    (
        "repo_ops_py_poetry.poetry.run",
        apply_poetry,
        "py",
        "poetry",
        66,
        _poetry_extra_patches,
    ),
]


@pytest.mark.parametrize(
    "patch_target,apply_fn,ecosystem,manager,env_val,extra_patches",
    _APPLY_CASES,
    ids=[manager for _, _, _, manager, _, _ in _APPLY_CASES],
)
def test_timeout_apply_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_workspace,
    patch_target,
    apply_fn,
    ecosystem,
    manager,
    env_val,
    extra_patches,
) -> None:
    """REPO_OPS_TIMEOUT_APPLY overrides the timeout passed to run() in each applier."""
    monkeypatch.setenv("REPO_OPS_TIMEOUT_APPLY", str(env_val))
    captured: list[dict] = []

    with ExitStack() as stack:
        for extra in extra_patches(monkeypatch):
            stack.enter_context(extra)
        stack.enter_context(patch(patch_target, side_effect=_fake_run(captured)))
        apply_fn(make_workspace(tmp_path, ecosystem=ecosystem, manager=manager, label="demo"))

    assert captured, "run() was never called"
    assert all(c["timeout"] == env_val for c in captured), (
        f"expected all timeouts={env_val}, got {[c['timeout'] for c in captured]}"
    )


# ---------------------------------------------------------------------------
# Default (no env set) still uses the original constant values
# ---------------------------------------------------------------------------


def test_timeout_check_default_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_workspace,
) -> None:
    """Without env override, check functions use TIMEOUT_CHECK."""
    monkeypatch.delenv("REPO_OPS_TIMEOUT_CHECK", raising=False)
    captured: list[dict] = []

    from repo_ops_py_uv.uv import check_uv

    with patch("repo_ops_py.uv_lock.run", side_effect=_fake_run(captured)):
        check_uv(make_workspace(tmp_path, ecosystem="py", manager="uv", label="demo"))

    assert captured[0]["timeout"] == TIMEOUT_CHECK


def test_timeout_apply_default_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_workspace,
) -> None:
    """Without env override, apply functions use TIMEOUT_APPLY."""
    monkeypatch.delenv("REPO_OPS_TIMEOUT_APPLY", raising=False)
    monkeypatch.setenv("REPO_OPS_RUST_TARGETED_BUMP", "0")
    captured: list[dict] = []

    from repo_ops_rust.rust import apply_rust

    with patch("repo_ops_rust.rust.run", side_effect=_fake_run(captured)):
        apply_rust(make_workspace(tmp_path, ecosystem="rust", manager="cargo", label="demo"))

    assert captured[0]["timeout"] == TIMEOUT_APPLY
