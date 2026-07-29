"""Plan/check must not invoke corepack_prepare; which managers it acts on."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_js.workspaces import corepack_prepare
from repo_ops_models import Change, Workspace
from repo_ops_upgrade import registry


def test_check_workspace_does_not_call_corepack_prepare(tmp_path: Path):
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="",
    )

    def fake_checker(_ws: Workspace):
        return []

    with (
        patch("repo_ops_upgrade.registry.corepack_prepare") as mock_prepare,
        patch.dict(
            registry.CHECKERS,
            {"pnpm": fake_checker},
            clear=False,
        ),
    ):
        result = registry.check_workspace(ws)

    mock_prepare.assert_not_called()
    assert result.status == "ok"


def test_apply_workspace_still_calls_corepack_prepare(tmp_path: Path):
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="",
    )

    with (
        patch("repo_ops_upgrade.registry.corepack_prepare") as mock_prepare,
        patch.dict(registry.APPLIERS, {"pnpm": lambda _ws: None}, clear=False),
    ):
        registry.apply_workspace(ws)

    mock_prepare.assert_called_once_with("pnpm", tmp_path)


def test_check_workspace_three_tuple_sets_overrides_and_behind(tmp_path: Path) -> None:
    """#110: yarn's (changes, overrides_audit, behind_latest) 3-tuple is unpacked."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )

    def fake_checker(_ws: Workspace):
        return (
            [Change("next", "16.2.6", "16.2.7")],
            [{"package": "lodash", "kind": "override"}],
            [Change("@sentry/nextjs", "10.55.0", "10.56.0")],
        )

    with patch.dict(registry.CHECKERS, {"yarn": fake_checker}, clear=False):
        result = registry.check_workspace(ws)

    assert result.status == "ok"
    assert [c.name for c in result.changes] == ["next"]
    assert result.overrides_audit == [{"package": "lodash", "kind": "override"}]
    assert result.behind_latest == [Change("@sentry/nextjs", "10.55.0", "10.56.0")]


def test_apply_workspace_returns_applier_behind_latest(tmp_path: Path) -> None:
    """#110: apply_workspace surfaces the behind_latest list an applier returns."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )
    behind = [Change("@sentry/nextjs", "10.55.0", "10.56.0")]
    with (
        patch("repo_ops_upgrade.registry.corepack_prepare"),
        patch.dict(registry.APPLIERS, {"yarn": lambda _ws: behind}, clear=False),
    ):
        out = registry.apply_workspace(ws)
    assert out == behind


def test_check_workspace_records_checker_failure(tmp_path: Path) -> None:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="pnpm",
        git_repo=tmp_path,
        label="",
    )

    def boom(_ws: Workspace):
        raise RuntimeError("pnpm outdated failed")

    with patch.dict(registry.CHECKERS, {"pnpm": boom}, clear=False):
        result = registry.check_workspace(ws)

    assert result.status == "fail"
    assert "pnpm outdated failed" in (result.error or "")


def _write_manifest(mdir: Path, pm: str | None) -> None:
    pkg: dict[str, str] = {"name": "app"}
    if pm is not None:
        pkg["packageManager"] = pm
    (mdir / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


def _recording_run(
    calls: list[list[str]],
    result: subprocess.CompletedProcess[str] | None = None,
):
    def fake_run(cmd: list[str], cwd, timeout: float = 300, env=None):
        calls.append(cmd)
        if result is not None and cmd[:2] == ["corepack", "prepare"]:
            return result
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return fake_run


def test_corepack_prepare_skips_bun(tmp_path: Path, capsys) -> None:
    """Corepack ships no bun shim, so a bun workspace must not touch it at all."""
    _write_manifest(tmp_path, "bun@1.3.14")
    calls: list[list[str]] = []

    with patch("repo_ops_js.workspaces.run", _recording_run(calls)):
        corepack_prepare("bun", tmp_path)

    assert calls == []
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("manager", ["yarn", "pnpm", "npm"])
def test_corepack_prepare_activates_supported_managers(
    manager: str, tmp_path: Path
) -> None:
    _write_manifest(tmp_path, f"{manager}@1.2.3")
    calls: list[list[str]] = []

    with patch("repo_ops_js.workspaces.run", _recording_run(calls)):
        corepack_prepare(manager, tmp_path)

    assert calls == [
        ["corepack", "enable"],
        ["corepack", "prepare", f"{manager}@1.2.3", "--activate"],
    ]


def test_corepack_prepare_skips_unsupported_manager_pin(tmp_path: Path) -> None:
    """An unsupported packageManager pin is skipped even if the manager differs."""
    _write_manifest(tmp_path, "bun@1.3.14")
    calls: list[list[str]] = []

    with patch("repo_ops_js.workspaces.run", _recording_run(calls)):
        corepack_prepare("yarn", tmp_path)

    assert calls == [["corepack", "enable"]]


def test_corepack_prepare_warn_falls_back_to_stdout(tmp_path: Path, capsys) -> None:
    """Corepack writes Usage Errors to stdout, leaving an empty-stderr warn useless."""
    _write_manifest(tmp_path, "yarn@4.10.3")
    failure = subprocess.CompletedProcess(
        ["corepack", "prepare"],
        1,
        stdout="Usage Error: Unsupported package manager specification (yarn@4.10.3)\n",
        stderr="",
    )

    with patch("repo_ops_js.workspaces.run", _recording_run([], failure)):
        corepack_prepare("yarn", tmp_path)

    err = capsys.readouterr().err
    assert "WARN corepack prepare yarn@4.10.3 --activate failed:" in err
    assert "Unsupported package manager specification" in err
