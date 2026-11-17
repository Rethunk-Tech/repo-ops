"""Manifest writers must not write through a symlink that leaves the repository."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_pnpm import pnpm
from repo_ops_models import Change, Workspace
from repo_ops_nuget import nuget
from repo_ops_py_pip_tools import pip_tools
from repo_ops_upgrade_remediations_js import remediations


def _repo_workspace(tmp_path: Path, manager: str = "pnpm") -> Workspace:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return Workspace(path=repo, git_repo=repo, ecosystem="node", manager=manager, label="repo")


def test_pnpm_catalog_rewrite_does_not_write_through_symlink(tmp_path: Path) -> None:
    ws = _repo_workspace(tmp_path)
    outside = tmp_path / "outside" / "pnpm-workspace.yaml"
    outside.parent.mkdir()
    outside.write_text("catalog:\n  react: 18.2.0\n", encoding="utf-8")
    (ws.path / "pnpm-workspace.yaml").symlink_to(outside)

    assert pnpm._rewrite_catalog_package(ws, "react", "18.3.0") is False
    assert outside.read_text(encoding="utf-8") == "catalog:\n  react: 18.2.0\n"


def test_pm_pin_does_not_write_through_symlink(tmp_path: Path) -> None:
    ws = _repo_workspace(tmp_path, manager="npm")
    outside = tmp_path / "outside" / "package.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps({"packageManager": "npm@10.0.0"}), encoding="utf-8")
    (ws.path / "package.json").symlink_to(outside)

    with patch.object(remediations, "_pm_target_version", return_value="10.9.0"):
        ok, detail = remediations.apply_pm_pin(ws, {})

    assert ok is False
    assert "outside the repo" in detail
    assert json.loads(outside.read_text(encoding="utf-8"))["packageManager"] == "npm@10.0.0"


def test_pip_tools_bump_does_not_write_through_symlink(tmp_path: Path) -> None:
    ws = _repo_workspace(tmp_path)
    ws = replace(ws, ecosystem="python", manager="pip-tools")
    outside = tmp_path / "outside" / "requirements.in"
    outside.parent.mkdir()
    outside.write_text("flask>=2.0\n", encoding="utf-8")
    (ws.path / "requirements.in").symlink_to(outside)

    remaining = pip_tools._apply_pip_tools_targeted_bumps(ws, [Change("flask", "2.0", "3.0")])

    assert remaining == [Change("flask", "2.0", "3.0")]
    assert outside.read_text(encoding="utf-8") == "flask>=2.0\n"


def test_nuget_bump_does_not_write_through_symlink(tmp_path: Path) -> None:
    ws = _repo_workspace(tmp_path)
    ws = replace(ws, ecosystem="nuget", manager="nuget")
    outside = tmp_path / "outside" / "app.csproj"
    outside.parent.mkdir()
    outside.write_text(
        '<Project><ItemGroup><PackageReference Include="Serilog" Version="1.0.0" /></ItemGroup>'
        "</Project>",
        encoding="utf-8",
    )
    (ws.path / "app.csproj").symlink_to(outside)

    with patch.object(
        nuget, "_candidates", return_value=([Change("Serilog", "1.0.0", "2.0.0")], [])
    ):
        nuget.apply_nuget(ws)

    assert "1.0.0" in outside.read_text(encoding="utf-8")
