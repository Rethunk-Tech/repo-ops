"""pnpm wiring for package version-ceiling ("hold") rules."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_js_pnpm.pnpm import (
    apply_pnpm,
    check_pnpm,
    clear_pnpm_audit_version_overrides,
    set_pnpm_audit_version_overrides,
    targeted_bump_behind,
)
from repo_ops_models import Change

_RESOLVER_PATH = "repo_ops_lockfiles.snapshots.resolved_version_from_node_modules"
_OUTDATED = "repo_ops_js_pnpm.pnpm._pnpm_outdated"
_GLOBAL_HOLDS_CACHE = "repo_ops_js.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_js.holds._repo_holds_cached"


def _seed(d: Path, *, pkg_content: str | None = None) -> None:
    if pkg_content is None:
        pkg_content = json.dumps({"dependencies": {"typescript": "^6.0.0"}})
    (d / "package.json").write_text(pkg_content, encoding="utf-8")
    (d / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")


def _held_rule(**overrides):
    from repo_ops_core.holds import HoldRule

    defaults = {"package": "typescript", "max_version": "6"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def test_targeted_bump_uses_hold_ceiling_instead_of_latest(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """A held package's targeted bump targets `pnpm add pkg@<=ceiling`, not --latest."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_fake_run),
        patch(_RESOLVER_PATH, return_value="6.9.0"),
    ):
        targeted_bump_behind(ws, [Change("typescript", "6.5.0", "7.0.0")])

    joined = [" ".join(c) for c in captured_cmds]
    assert any(c == ["pnpm", "add", "typescript@<=6"] for c in captured_cmds), joined
    assert not any(c[:2] == ["pnpm", "update"] for c in captured_cmds)


def test_targeted_bump_audit_override_wins_over_hold_ceiling(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """A security remediation (audit --fix) must not be blocked by a hold."""
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    set_pnpm_audit_version_overrides({"typescript": "6.2.5"})
    try:
        with (
            patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
            patch("repo_ops_js_pnpm.pnpm.run", side_effect=_fake_run),
            patch(_RESOLVER_PATH, return_value="6.2.5"),
        ):
            targeted_bump_behind(ws, [Change("typescript", "6.0.0", "6.2.5")])
    finally:
        clear_pnpm_audit_version_overrides()

    assert any(c == ["pnpm", "add", "typescript@6.2.5"] for c in captured_cmds)
    assert not any(c[:2] == ["pnpm", "update"] for c in captured_cmds)


def test_check_pnpm_does_not_promote_held_package_past_ceiling(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            _OUTDATED,
            return_value=([], [Change("typescript", "6.5.0", "7.0.0")]),
        ),
    ):
        changes, behind = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )
    assert "typescript" not in {c.name for c in changes}
    assert behind == [Change("typescript", "6.5.0", "7.0.0")]


def test_check_pnpm_promotes_held_package_within_ceiling(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """A behind-latest bump that stays within the ceiling is still promoted."""
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            _OUTDATED,
            return_value=([], [Change("typescript", "6.5.0", "6.9.0")]),
        ),
    ):
        changes, behind = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )
    assert "typescript" in {c.name for c in changes}
    assert behind == []


def test_apply_pnpm_clamps_package_already_past_ceiling(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """A package already on 7.x when a hold is added gets clamped back down.

    Simulates the retroactive-hold case: `pnpm outdated` reports nothing (the
    manifest range already matches what's installed) but the resolved version
    on disk violates a hold ceiling added after the fact.
    """
    _seed(tmp_path, pkg_content=json.dumps({"dependencies": {"typescript": "^7.0.0"}}))
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")

    nm_dir = tmp_path / "node_modules" / "typescript"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(
        json.dumps({"version": "7.2.0"}), encoding="utf-8"
    )

    def _fake_run(cmd, cwd, timeout=600, env=None):
        if "add" in cmd and any("typescript@" in c for c in cmd):
            (nm_dir / "package.json").write_text(
                json.dumps({"version": "6.9.0"}), encoding="utf-8"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(_OUTDATED, return_value=([], [])),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_fake_run),
    ):
        behind = apply_pnpm(ws)

    assert len(behind) == 1
    assert behind[0].name == "typescript"
    assert behind[0].from_ver == "6.9.0"
    assert behind[0].to == "7.2.0"


def test_apply_pnpm_raises_when_clamp_cannot_satisfy_ceiling(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """A corrective install that still can't land under ceiling is a loud failure."""
    _seed(tmp_path, pkg_content=json.dumps({"dependencies": {"typescript": "^7.0.0"}}))
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")

    nm_dir = tmp_path / "node_modules" / "typescript"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(
        json.dumps({"version": "7.2.0"}), encoding="utf-8"
    )

    def _fake_run(cmd, cwd, timeout=600, env=None):
        # Corrective install "succeeds" but node_modules is never rewritten —
        # simulates a ceiling no published version can satisfy.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(_OUTDATED, return_value=([], [])),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_fake_run),
        pytest.raises(RuntimeError, match="hold"),
    ):
        apply_pnpm(ws)


def _seed_is_odd_pin(d: Path) -> None:
    pkg = json.dumps(
        {
            "dependencies": {"is-odd": ">=0.1.0 <2.0.0"},
            "pnpm": {"overrides": {"is-odd": "0.1.2"}},
        }
    )
    (d / "package.json").write_text(pkg, encoding="utf-8")
    (d / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")
    nm_dir = d / "node_modules" / "is-odd"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(
        json.dumps({"version": "0.1.2"}), encoding="utf-8"
    )


def test_targeted_bump_releases_unheld_overrides_pin(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """An unheld package's pnpm overrides pin is removed before the bump."""
    _seed_is_odd_pin(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout=600, env=None):
        captured_cmds.append(cmd)
        if "is-odd" in cmd:
            (tmp_path / "node_modules" / "is-odd" / "package.json").write_text(
                json.dumps({"version": "1.0.0"}), encoding="utf-8"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=()),
        patch(_REPO_HOLDS_CACHE, return_value=()),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_fake_run),
    ):
        applied, behind = targeted_bump_behind(ws, [Change("is-odd", "0.1.2", "1.0.0")])

    assert captured_cmds == [["pnpm", "update", "--latest", "is-odd"]]
    assert not behind
    assert applied == [Change("is-odd", "0.1.2", "1.0.0")]
    pkg_after = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert "is-odd" not in pkg_after.get("pnpm", {}).get("overrides", {})


def test_targeted_bump_keeps_held_overrides_pin(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """An active hold leaves the package's pnpm overrides pin in place."""
    _seed_is_odd_pin(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")

    def _fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(
            _GLOBAL_HOLDS_CACHE,
            return_value=(_held_rule(package="is-odd", max_version="0.1.2"),),
        ),
        patch(_REPO_HOLDS_CACHE, return_value=()),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=_fake_run),
        patch(_RESOLVER_PATH, return_value="0.1.2"),
        patch("repo_ops_js_pnpm.pnpm.release_unheld_pin") as release_pin,
    ):
        targeted_bump_behind(ws, [Change("is-odd", "0.1.2", "1.0.0")])

    release_pin.assert_not_called()
    pkg_after = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert pkg_after["pnpm"]["overrides"]["is-odd"] == "0.1.2"
