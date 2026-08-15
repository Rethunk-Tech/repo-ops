"""Tests for pnpm workspace catalogs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_pnpm.pnpm import (
    _catalog_spec_from_version,
    _catalog_specs,
    _pnpm_outdated,
    apply_pnpm,
    check_pnpm,
    targeted_bump_behind,
)
from repo_ops_models import Change

_GLOBAL_HOLDS_CACHE = "repo_ops_js.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_js.holds._repo_holds_cached"


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
    tmp_path: Path, make_workspace,
) -> None:
    _seed(tmp_path)

    assert _catalog_specs(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")) == {
        "react": ["^18.2.0", "^18.2.0"],
    }


def test_catalog_spec_from_version_strips_v_prefix() -> None:
    assert _catalog_spec_from_version("^v18.2.0") == "18.2.0"


def test_catalog_drift_uses_lowest_behind_duplicate_spec(tmp_path: Path, make_workspace) -> None:
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
        changes, behind = check_pnpm(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == [Change("react", "18.2.0", "18.3.0")]
    assert behind == [Change("react", "18.2.0", "19.0.0")]


def test_epoch_catalog_pin_is_not_treated_as_protocol(tmp_path: Path, make_workspace) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        """\
catalog:
  epoch-pkg: "1:2.3.4"
  local-only: workspace:*
""",
        encoding="utf-8",
    )

    assert _catalog_specs(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")) == {"epoch-pkg": ["1:2.3.4"]}


def test_catalog_drift_is_behind_latest_without_targeted_bump(
    tmp_path: Path, monkeypatch, make_workspace
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
        changes, behind = check_pnpm(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == [Change("react", "18.2.0", "18.3.0")]
    assert behind == [Change("react", "18.2.0", "19.0.0")]


def test_catalog_drift_does_not_double_count_in_range_bump(tmp_path: Path, make_workspace) -> None:
    _seed(tmp_path)
    outdated = json.dumps(
        {"react": {"current": "18.2.0", "wanted": "18.3.0", "latest": "18.3.0"}}
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, 1, stdout=outdated, stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == [Change("react", "18.2.0", "18.3.0")]
    assert behind == []


def test_catalog_drift_is_reported_when_outdated_omits_package(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    _seed(tmp_path)
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", "1")

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:3] == ["pnpm", "view", "react"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="19.0.0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="{}", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == []
    assert behind == [Change("react", "18.2.0", "19.0.0")]


def test_missing_catalog_row_skips_view_without_latest_flags(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    _seed(tmp_path)
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    monkeypatch.delenv("REPO_OPS_PNPM_UPDATE_LATEST", raising=False)
    commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, stdout="{}", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = check_pnpm(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == []
    assert behind == []
    assert all(cmd[:2] != ["pnpm", "view"] for cmd in commands)


def test_missing_catalog_row_from_ver_uses_lockfile_resolved(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    _seed(tmp_path)
    (tmp_path / "pnpm-lock.yaml").write_text(
        """\
lockfileVersion: '6.0'
packages:
  react@18.3.1:
    resolution: {integrity: sha512-test}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        commands.append(list(cmd))
        if cmd[:3] == ["pnpm", "view", "react"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="19.0.0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="{}", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = _pnpm_outdated(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == []
    assert behind == [Change("react", "18.3.1", "19.0.0")]
    assert commands == [
        ["pnpm", "outdated", "--format", "json"],
        ["pnpm", "view", "react", "version"],
    ]


def test_missing_catalog_row_ignores_lockfile_prefix_collisions(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    _seed(tmp_path)
    (tmp_path / "pnpm-lock.yaml").write_text(
        """\
lockfileVersion: '6.0'
packages:
  react-dom@17.0.0:
    resolution: {integrity: sha512-dom}
  react@18.3.1:
    resolution: {integrity: sha512-react}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:3] == ["pnpm", "view", "react"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="19.0.0\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="{}", stderr="")

    with patch("repo_ops_js_pnpm.pnpm.run", side_effect=fake_run):
        changes, behind = _pnpm_outdated(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"))

    assert changes == []
    assert behind == [Change("react", "18.3.1", "19.0.0")]


def test_targeted_catalog_bump_updates_default_and_named_tables(
    tmp_path: Path, monkeypatch, make_workspace
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
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), [Change("react", "^18.2.0", "19.0.0")]
        )

    assert applied == [Change("react", "^18.2.0", "19.0.0")]
    assert behind == []
    assert commands == [["pnpm", "install"]]
    assert (tmp_path / "pnpm-workspace.yaml").read_text(encoding="utf-8").count(
        "^19.0.0"
    ) == 2


def test_targeted_catalog_bump_restores_workspace_on_install_failure(
    tmp_path: Path, monkeypatch, make_workspace
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
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), [Change("react", "^18.2.0", "19.0.0")]
        )

    assert applied == []
    assert behind == [Change("react", "^18.2.0", "19.0.0")]
    assert (tmp_path / "pnpm-workspace.yaml").read_bytes() == workspace_before
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == lock_before


def test_targeted_catalog_bump_restores_ghost_install(
    tmp_path: Path, monkeypatch, make_workspace
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
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), [Change("react", "^18.2.0", "19.0.0")]
        )

    assert applied == []
    assert behind == [Change("react", "^18.2.0", "19.0.0")]
    assert (tmp_path / "pnpm-workspace.yaml").read_bytes() == workspace_before
    assert (tmp_path / "pnpm-lock.yaml").read_bytes() == lock_before


def test_targeted_catalog_bump_applies_hold_ceiling_to_all_tables(
    tmp_path: Path, monkeypatch, make_workspace
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
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), [Change("react", "^5.2.0", "7.0.0")]
        )

    assert applied == [Change("react", "^5.2.0", "6.0.0")]
    assert behind == []
    assert (tmp_path / "pnpm-workspace.yaml").read_text(encoding="utf-8").count(
        "<=6"
    ) == 2


def test_apply_pnpm_targets_catalog_drift(tmp_path: Path, monkeypatch, make_workspace) -> None:
    _seed(tmp_path)
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
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
