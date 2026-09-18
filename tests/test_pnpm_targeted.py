"""Tests for pnpm targeted behind-latest bumps."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_pnpm.pnpm import targeted_bump_behind
from repo_ops_models import Change

_RESOLVER_PATH = "repo_ops_lockfiles.snapshots.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_pnpm.pnpm._pnpm_outdated"

_VUE_BEHIND = [Change("vue", "3.0.0", "4.0.0")]


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _seed(d: Path) -> None:
    """Write minimal package.json and pnpm-lock.yaml to *d*."""
    (d / "package.json").write_text(
        json.dumps({"dependencies": {"vue": "^3.0.0"}}), encoding="utf-8"
    )
    (d / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# targeted_bump_behind: success path
# ---------------------------------------------------------------------------


def _pnpm_update_run(tmp_path: Path, *, landed: str = "4.0.0", update_rc: int = 0):
    """Fake run: successful pnpm update --latest writes the landed version."""
    nm_dir = tmp_path / "node_modules" / "vue"

    def _run(cmd, cwd, timeout=600, env=None):
        if cmd[:3] == ["pnpm", "update", "--latest"]:
            if update_rc == 0:
                nm_dir.mkdir(parents=True, exist_ok=True)
                (nm_dir / "package.json").write_text(
                    json.dumps({"version": landed}), encoding="utf-8"
                )
            return subprocess.CompletedProcess(cmd, update_rc, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


def test_targeted_bump_moves_the_gap(tmp_path: Path, monkeypatch, make_workspace) -> None:
    _seed(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    with (
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_pnpm_update_run(tmp_path)),
        patch(_RESOLVER_PATH, return_value="4.0.0"),
    ):
        applied, still_behind = targeted_bump_behind(ws, _VUE_BEHIND)
    assert applied == [Change("vue", "3.0.0", "4.0.0")]
    assert still_behind == []


# ---------------------------------------------------------------------------
# targeted_bump_behind: revert paths
# ---------------------------------------------------------------------------


def test_targeted_bump_reverts_when_update_fails(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    _seed(tmp_path)
    original_lock = (tmp_path / "pnpm-lock.yaml").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    with (
        patch(
            "repo_ops_js_pnpm.pnpm.run",
            side_effect=_pnpm_update_run(tmp_path, update_rc=1),
        ),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        applied, still_behind = targeted_bump_behind(ws, _VUE_BEHIND)
    assert applied == []
    assert still_behind == _VUE_BEHIND
    # Lockfile must be restored.
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == original_lock


def test_targeted_bump_reverts_ghost_bump(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """pnpm update exits 0 but node_modules still has the old version → revert."""
    _seed(tmp_path)
    original_lock = (tmp_path / "pnpm-lock.yaml").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        # Resolver returns the old version — no advance (ghost bump).
        patch(_RESOLVER_PATH, return_value="3.0.0"),
    ):
        applied, still_behind = targeted_bump_behind(ws, _VUE_BEHIND)
    assert applied == []
    assert still_behind == _VUE_BEHIND
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == original_lock


def test_targeted_bump_reverts_when_resolver_returns_none(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """If node_modules is unreadable, treat it as no-op and revert."""
    _seed(tmp_path)
    original_lock = (tmp_path / "pnpm-lock.yaml").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    with (
        patch("repo_ops_js_pnpm.pnpm.run", _fake_run_ok),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        applied, still_behind = targeted_bump_behind(ws, _VUE_BEHIND)
    assert applied == []
    assert still_behind == _VUE_BEHIND
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == original_lock


# ---------------------------------------------------------------------------
# targeted_bump_behind: pnpm update --latest command shape
# ---------------------------------------------------------------------------


def test_targeted_bump_uses_update_latest_command(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Verifies the exact pnpm command issued: pnpm update --latest <pkg>."""
    _seed(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    captured_cmds: list[list[str]] = []

    def _capture_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(list(cmd))
        nm = tmp_path / "node_modules" / "vue"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(json.dumps({"version": "4.0.0"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_capture_run),
        patch(_RESOLVER_PATH, return_value="4.0.0"),
    ):
        targeted_bump_behind(ws, _VUE_BEHIND)

    update_cmds = [c for c in captured_cmds if c[:2] == ["pnpm", "update"]]
    assert update_cmds, "expected at least one pnpm update call"
    assert update_cmds[0] == ["pnpm", "update", "--latest", "vue"]


# ---------------------------------------------------------------------------
# apply_pnpm returns still-behind list
# ---------------------------------------------------------------------------


def test_pnpm_targeted_bump_restores_snapshot_when_guard_raises(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Guard raise must restore lockfile (npm/bun parity)."""
    _seed(tmp_path)
    original_lock = (tmp_path / "pnpm-lock.yaml").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")

    def fake_run(cmd, cwd, timeout=600, env=None):
        (tmp_path / "pnpm-lock.yaml").write_text("mutated-lock\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_guard(ws, requested, manager="pnpm"):
        raise RuntimeError("ghost bump: pnpm upgrade blocked")

    with (
        patch("repo_ops_js_pnpm.pnpm.run", fake_run),
        patch(_RESOLVER_PATH, return_value="4.0.0"),
        patch("repo_ops_js_pnpm.pnpm.assert_targets_resolved", fake_guard),
    ):
        applied, still_behind = targeted_bump_behind(ws, _VUE_BEHIND)

    assert applied == []
    assert still_behind == _VUE_BEHIND
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == original_lock
