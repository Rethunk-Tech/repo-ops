"""Tests for npm targeted behind-latest bumps (issue #122)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_npm.npm import (
    _save_flag_for,
    apply_npm,
    targeted_bump_behind,
)
from repo_ops_models import Change, Workspace

_RESOLVER_PATH = "repo_ops_lockfiles.snapshots.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_npm.npm._npm_outdated"

_REACT_BEHIND = [Change("react", "18.0.0", "19.0.0")]


def _fake_run_ok(cmd, cwd, timeout=600, env=None):
    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _seed(d: Path, *, pkg_content: str | None = None) -> None:
    """Write a minimal package.json and package-lock.json to *d*."""
    if pkg_content is None:
        pkg_content = json.dumps({"dependencies": {"react": "^18.0.0"}})
    (d / "package.json").write_text(pkg_content, encoding="utf-8")
    (d / "package-lock.json").write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------
# _save_flag_for unit tests
# ---------------------------------------------------------------------------


def test_save_flag_for_peer_dep() -> None:
    pkg: dict[str, object] = {"peerDependencies": {"react": "^18.0.0"}}
    assert _save_flag_for("react", pkg) == ["--save-peer"]


def test_save_flag_for_regular_dep() -> None:
    pkg: dict[str, object] = {"dependencies": {"react": "^18.0.0"}}
    assert _save_flag_for("react", pkg) == ["--save"]


def test_save_flag_for_dev_dep() -> None:
    pkg: dict[str, object] = {"devDependencies": {"typescript": "^5.0.0"}}
    assert _save_flag_for("typescript", pkg) == ["--save-dev"]


def test_save_flag_for_optional_dep() -> None:
    pkg: dict[str, object] = {"optionalDependencies": {"fsevents": "^2.0.0"}}
    assert _save_flag_for("fsevents", pkg) == ["--save-optional"]


def test_save_flag_for_exact_pin_adds_save_exact() -> None:
    """Exact pin (no ^/~) → adds --save-exact to preserve the range style."""
    pkg: dict[str, object] = {"dependencies": {"lodash": "4.17.20"}}
    flags = _save_flag_for("lodash", pkg)
    assert "--save" in flags
    assert "--save-exact" in flags


def test_save_flag_for_tilde_range_no_exact() -> None:
    pkg: dict[str, object] = {"dependencies": {"axios": "~1.6.0"}}
    flags = _save_flag_for("axios", pkg)
    assert "--save" in flags
    assert "--save-exact" not in flags


def test_save_flag_for_unknown_package_defaults_to_save() -> None:
    """Package not found in any dep section → defaults to --save."""
    pkg: dict[str, object] = {"dependencies": {"other": "^1.0.0"}}
    assert _save_flag_for("unknown-pkg", pkg) == ["--save"]


# ---------------------------------------------------------------------------
# targeted_bump_behind: env gate
# ---------------------------------------------------------------------------


def test_targeted_bump_disabled_by_default(tmp_path: Path, monkeypatch, make_workspace) -> None:
    _seed(tmp_path)
    monkeypatch.delenv("REPO_OPS_NPM_TARGETED_BUMP", raising=False)
    run_mock = patch("repo_ops_js_npm.npm.run")
    with run_mock as mock_run:
        applied, still_behind = targeted_bump_behind(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"), _REACT_BEHIND)
    assert applied == []
    assert still_behind == _REACT_BEHIND
    mock_run.assert_not_called()  # opt-in: no npm invocations when disabled


# ---------------------------------------------------------------------------
# targeted_bump_behind: success path
# ---------------------------------------------------------------------------


def _npm_install_run(tmp_path: Path, *, landed: str = "19.0.0", install_rc: int = 0):
    """Fake run: successful npm install writes the landed version to node_modules."""
    nm_dir = tmp_path / "node_modules" / "react"

    def _run(cmd, cwd, timeout=600, env=None):
        if "install" in cmd and "react@latest" in " ".join(cmd):
            if install_rc == 0:
                nm_dir.mkdir(parents=True, exist_ok=True)
                (nm_dir / "package.json").write_text(
                    json.dumps({"version": landed}), encoding="utf-8"
                )
            return subprocess.CompletedProcess(cmd, install_rc, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


def test_targeted_bump_moves_the_gap(tmp_path: Path, monkeypatch, make_workspace) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_npm_install_run(tmp_path)),
        patch(_RESOLVER_PATH, return_value="19.0.0"),
    ):
        applied, still_behind = targeted_bump_behind(ws, _REACT_BEHIND)
    assert applied == [Change("react", "18.0.0", "19.0.0")]
    assert still_behind == []


# ---------------------------------------------------------------------------
# targeted_bump_behind: revert paths
# ---------------------------------------------------------------------------


def test_targeted_bump_reverts_when_install_fails(tmp_path: Path, monkeypatch, make_workspace) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    original_lock = (tmp_path / "package-lock.json").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with (
        patch(
            "repo_ops_js_npm.npm.run",
            side_effect=_npm_install_run(tmp_path, install_rc=1),
        ),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        applied, still_behind = targeted_bump_behind(ws, _REACT_BEHIND)
    assert applied == []
    assert still_behind == _REACT_BEHIND
    # Lockfile must be restored.
    assert (tmp_path / "package-lock.json").read_bytes() == original_lock


def test_targeted_bump_reverts_ghost_bump(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """npm install exits 0 but node_modules still has the old version → revert."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    original_lock = (tmp_path / "package-lock.json").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        # Resolver returns the old version — no advance.
        patch(_RESOLVER_PATH, return_value="18.0.0"),
    ):
        applied, still_behind = targeted_bump_behind(ws, _REACT_BEHIND)
    assert applied == []
    assert still_behind == _REACT_BEHIND
    assert (tmp_path / "package-lock.json").read_bytes() == original_lock


def test_npm_targeted_bump_restores_snapshot_when_guard_raises(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Guard raise must restore lockfile after a targeted install."""
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    _seed(tmp_path)
    original_lock = (tmp_path / "package-lock.json").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")

    def fake_run(cmd, cwd, timeout=600, env=None):
        (tmp_path / "package-lock.json").write_text("mutated-lock\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_guard(ws, requested, manager="npm"):
        raise RuntimeError("ghost bump: npm upgrade blocked")

    with (
        patch("repo_ops_js_npm.npm.run", fake_run),
        patch(
            "repo_ops_js_npm.npm.resolved_version_from_node_modules",
            return_value="19.0.0",
        ),
        patch("repo_ops_js_npm.npm.assert_targets_resolved", fake_guard),
    ):
        applied, still_behind = targeted_bump_behind(ws, _REACT_BEHIND)

    assert applied == []
    assert still_behind == _REACT_BEHIND
    assert (tmp_path / "package-lock.json").read_bytes() == original_lock


def test_targeted_bump_reverts_when_resolver_returns_none(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """If node_modules is unreadable, treat it as a no-op and revert."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    original_lock = (tmp_path / "package-lock.json").read_bytes()
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(_RESOLVER_PATH, return_value=None),
    ):
        applied, still_behind = targeted_bump_behind(ws, _REACT_BEHIND)
    assert applied == []
    assert still_behind == _REACT_BEHIND
    assert (tmp_path / "package-lock.json").read_bytes() == original_lock


# ---------------------------------------------------------------------------
# apply_npm returns still-behind list
# ---------------------------------------------------------------------------


def test_apply_npm_returns_empty_behind_when_targeted_disabled(
    monkeypatch,
) -> None:
    """With targeted bump disabled, apply_npm returns the full behind_latest list."""
    monkeypatch.delenv("REPO_OPS_NPM_TARGETED_BUMP", raising=False)
    ws = Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="npm",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )
    with (
        patch("repo_ops_js_npm.npm.run", _fake_run_ok),
        patch(_OUTDATED, return_value=([], [Change("react", "18.0.0", "19.0.0")])),
    ):
        behind = apply_npm(ws)
    # targeted bump disabled → still-behind is the full original behind list.
    assert behind == [Change("react", "18.0.0", "19.0.0")]


def test_apply_npm_targeted_clears_behind(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """Targeted bump enabled + successful → apply_npm returns empty behind."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_npm_install_run(tmp_path)),
        patch(_OUTDATED, return_value=([], [Change("react", "18.0.0", "19.0.0")])),
        patch(_RESOLVER_PATH, return_value="19.0.0"),
    ):
        behind = apply_npm(ws)
    assert behind == []


def test_check_npm_promotes_behind_when_targeted_enabled(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Plan preview: targeted-bump candidates appear in changes (yarn parity)."""
    from repo_ops_js_npm.npm import check_npm

    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    with patch(
        _OUTDATED,
        return_value=(
            [Change("lodash", "4.17.20", "4.17.21")],
            [Change("react", "18.0.0", "19.0.0")],
        ),
    ):
        changes, behind = check_npm(make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo"))
    assert behind == []
    names = {c.name for c in changes}
    assert "lodash" in names
    assert "react" in names


def test_targeted_bump_accepts_on_env_value(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """REPO_OPS_NPM_TARGETED_BUMP=on must enable the gate (parity with pnpm/bun/yarn)."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "on")
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_npm_install_run(tmp_path)),
        patch(_RESOLVER_PATH, return_value="19.0.0"),
    ):
        applied, still_behind = targeted_bump_behind(ws, _REACT_BEHIND)
    assert applied == [Change("react", "18.0.0", "19.0.0")]
    assert still_behind == []


def test_targeted_bump_uses_save_dev_for_dev_dep(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """Dev dep → npm install --save-dev <pkg>@latest."""
    pkg_content = json.dumps({"devDependencies": {"typescript": "^5.0.0"}})
    _seed(tmp_path, pkg_content=pkg_content)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    captured_cmds: list[list[str]] = []

    def _capture_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(list(cmd))
        # Write version to node_modules so the check passes.
        nm = tmp_path / "node_modules" / "typescript"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(
            json.dumps({"version": "5.1.0"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_capture_run),
        patch(_RESOLVER_PATH, return_value="5.1.0"),
    ):
        targeted_bump_behind(ws, [Change("typescript", "5.0.0", "5.1.0")])

    install_cmds = [c for c in captured_cmds if "install" in c]
    assert install_cmds, "expected at least one npm install call"
    assert "--save-dev" in install_cmds[0]
    assert "typescript@latest" in install_cmds[0]


def test_targeted_bump_uses_save_peer_for_peer_dep(tmp_path: Path, monkeypatch, make_workspace) -> None:
    """Peer dep → npm install --save-peer <pkg>@latest."""
    pkg_content = json.dumps({"peerDependencies": {"react": "^18.0.0"}})
    _seed(tmp_path, pkg_content=pkg_content)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    captured_cmds: list[list[str]] = []

    def _capture_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(list(cmd))
        nm = tmp_path / "node_modules" / "react"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(
            json.dumps({"version": "19.0.0"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_capture_run),
        patch(_RESOLVER_PATH, return_value="19.0.0"),
    ):
        targeted_bump_behind(ws, _REACT_BEHIND)

    install_cmds = [c for c in captured_cmds if "install" in c]
    assert install_cmds, "expected at least one npm install call"
    assert "--save-peer" in install_cmds[0]
    assert "react@latest" in install_cmds[0]


def test_targeted_bump_uses_save_exact_for_exact_pin(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Exact-pin dep → npm install --save --save-exact <pkg>@latest."""
    pkg_content = json.dumps({"dependencies": {"lodash": "4.17.20"}})
    _seed(tmp_path, pkg_content=pkg_content)
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="demo")
    captured_cmds: list[list[str]] = []

    def _capture_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(list(cmd))
        nm = tmp_path / "node_modules" / "lodash"
        nm.mkdir(parents=True, exist_ok=True)
        (nm / "package.json").write_text(
            json.dumps({"version": "4.17.21"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js_npm.npm.run", side_effect=_capture_run),
        patch(_RESOLVER_PATH, return_value="4.17.21"),
    ):
        targeted_bump_behind(ws, [Change("lodash", "4.17.20", "4.17.21")])

    install_cmds = [c for c in captured_cmds if "install" in c]
    assert install_cmds
    assert "--save-exact" in install_cmds[0]
    assert "lodash@latest" in install_cmds[0]
