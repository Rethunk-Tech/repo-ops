"""Tests for pnpm workspace catalogs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_pnpm.pnpm import (
    _catalog_specs,
    apply_pnpm,
    check_pnpm,
    targeted_bump_behind,
)
from repo_ops_models import Change, Workspace

_GLOBAL_HOLDS_CACHE = "repo_ops_js.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_js.holds._repo_holds_cached"


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="demo",
    )


def _seed(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "catalog:"}}), encoding="utf-8"
    )
    (tmp_path / "pnpm-lock.yaml").write_text(
        "lockfileVersion: '6.0'\n", encoding="utf-8"
    )
    (tmp_path / "pnpm-workspace.yaml").write_text(
        """\
packages:
  - apps/*
catalog:
  react: ^18.2.0
  local-only: workspace:*
catalogs:
  legacy:
    react: ^18.2.0
    file-only: file:../shared
""",
        encoding="utf-8",
    )


def test_catalog_specs_reads_default_and_named_tables_skipping_protocols(
    tmp_path: Path,
) -> None:
    _seed(tmp_path)

    assert _catalog_specs(_ws(tmp_path)) == {
        "react": ["^18.2.0", "^18.2.0"],
    }


def test_catalog_drift_uses_lowest_behind_duplicate_spec(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / "pnpm-workspace.yaml").write_text(
        """\
catalog:
  react: ^18.2.0
catalogs:
  modern:
    react: ^19.0.0
""",
        encoding="utf-8",
    )
    outdated = json.dumps(
        {"react": {"current": "18.2.0", "wanted": "18.3.0", "latest": "19.0.0"}}
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 1, stdout=outdated, stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(_ws(tmp_path))

    assert changes == [Change("react", "18.2.0", "18.3.0")]
    assert behind == [Change("react", "18.2.0", "19.0.0")]


def test_epoch_catalog_pin_is_not_treated_as_protocol(tmp_path: Path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        """\
catalog:
  epoch-pkg: "1:2.3.4"
  local-only: workspace:*
""",
        encoding="utf-8",
    )

    assert _catalog_specs(_ws(tmp_path)) == {"epoch-pkg": ["1:2.3.4"]}


def test_catalog_drift_is_behind_latest_without_targeted_bump(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    monkeypatch.delenv("REPO_OPS_PNPM_UPDATE_LATEST", raising=False)
    outdated = json.dumps(
        {"react": {"current": "18.2.0", "wanted": "18.3.0", "latest": "19.0.0"}}
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 1, stdout=outdated, stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(_ws(tmp_path))

    assert changes == [Change("react", "18.2.0", "18.3.0")]
    assert behind == [Change("react", "18.2.0", "19.0.0")]


def test_catalog_drift_does_not_double_count_in_range_bump(tmp_path: Path) -> None:
    _seed(tmp_path)
    outdated = json.dumps(
        {"react": {"current": "18.2.0", "wanted": "18.3.0", "latest": "18.3.0"}}
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 1, stdout=outdated, stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(_ws(tmp_path))

    assert changes == [Change("react", "18.2.0", "18.3.0")]
    assert behind == []


def test_catalog_drift_is_reported_when_outdated_omits_package(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    monkeypatch.delenv("REPO_OPS_PNPM_UPDATE_LATEST", raising=False)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:3] == ["pnpm", "view", "react"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="19.0.0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="{}", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(_ws(tmp_path))

    assert changes == []
    assert behind == [Change("react", "18.2.0", "19.0.0")]


def test_targeted_catalog_bump_updates_default_and_named_tables(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        commands.append(list(cmd))
        node_modules = tmp_path / "node_modules" / "react"
        node_modules.mkdir(parents=True, exist_ok=True)
        (node_modules / "package.json").write_text(
            json.dumps({"version": "19.0.0"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        applied, behind = targeted_bump_behind(
            _ws(tmp_path), [Change("react", "^18.2.0", "19.0.0")]
        )

    assert applied == [Change("react", "^18.2.0", "19.0.0")]
    assert behind == []
    assert commands == [["pnpm", "install"]]
    assert (tmp_path / "pnpm-workspace.yaml").read_text(encoding="utf-8").count(
        "^19.0.0"
    ) == 2


def test_targeted_catalog_bump_restores_workspace_on_install_failure(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    workspace_before = (tmp_path / "pnpm-workspace.yaml").read_bytes()
    lock_before = (tmp_path / "pnpm-lock.yaml").read_bytes()

    def fake_run(cmd, cwd, timeout=600, env=None):
        (tmp_path / "pnpm-workspace.yaml").write_text(
            "catalog:\n  react: ^99.0.0\n", encoding="utf-8"
        )
        (tmp_path / "pnpm-lock.yaml").write_text("mutated\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="install failed")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        applied, behind = targeted_bump_behind(
            _ws(tmp_path), [Change("react", "^18.2.0", "19.0.0")]
        )

    assert applied == []
    assert behind == [Change("react", "^18.2.0", "19.0.0")]
    assert (tmp_path / "pnpm-workspace.yaml").read_bytes() == workspace_before
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == lock_before


def test_targeted_catalog_bump_restores_ghost_install(
    tmp_path: Path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    workspace_before = (tmp_path / "pnpm-workspace.yaml").read_bytes()
    lock_before = (tmp_path / "pnpm-lock.yaml").read_bytes()
    node_modules = tmp_path / "node_modules" / "react"
    node_modules.mkdir(parents=True)
    (node_modules / "package.json").write_text(
        json.dumps({"version": "18.2.0"}), encoding="utf-8"
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        (tmp_path / "pnpm-lock.yaml").write_text("mutated\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        applied, behind = targeted_bump_behind(
            _ws(tmp_path), [Change("react", "^18.2.0", "19.0.0")]
        )

    assert applied == []
    assert behind == [Change("react", "^18.2.0", "19.0.0")]
    assert (tmp_path / "pnpm-workspace.yaml").read_bytes() == workspace_before
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == lock_before


def test_targeted_catalog_bump_applies_hold_ceiling_to_all_tables(
    tmp_path: Path, monkeypatch
) -> None:
    from repo_ops_core.holds import HoldRule

    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    rule = HoldRule(package="react", max_version="6")
    workspace = tmp_path / "pnpm-workspace.yaml"
    workspace.write_text(
        workspace.read_text(encoding="utf-8").replace("^18.2.0", "^5.2.0"),
        encoding="utf-8",
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        node_modules = tmp_path / "node_modules" / "react"
        node_modules.mkdir(parents=True, exist_ok=True)
        (node_modules / "package.json").write_text(
            json.dumps({"version": "6.0.0"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(rule,)),
        patch(_REPO_HOLDS_CACHE, return_value=()),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run),
    ):
        applied, behind = targeted_bump_behind(
            _ws(tmp_path), [Change("react", "^5.2.0", "7.0.0")]
        )

    assert applied == [Change("react", "^5.2.0", "6.0.0")]
    assert behind == []
    assert (tmp_path / "pnpm-workspace.yaml").read_text(encoding="utf-8").count(
        "<=6"
    ) == 2


def test_apply_pnpm_targets_catalog_drift(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = _ws(tmp_path)
    catalog_change = Change("react", "^18.2.0", "19.0.0")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        calls.append(list(cmd))
        node_modules = tmp_path / "node_modules" / "react"
        node_modules.mkdir(parents=True, exist_ok=True)
        (node_modules / "package.json").write_text(
            json.dumps({"version": "19.0.0"}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_js_pnpm.pnpm._pnpm_outdated", return_value=([], [catalog_change])
        ),
        patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run),
    ):
        behind = apply_pnpm(ws)

    assert behind == []
    assert calls == [["pnpm", "install"]]
    assert "^19.0.0" in (tmp_path / "pnpm-workspace.yaml").read_text(encoding="utf-8")
