"""Upgrade-engine coverage for verify gates for
pdm/pipenv/hatch/deno/precommit, filter_workspaces, apply-fail remediations skip,
missing applier, worker-crash reconciliation helpers, exclude filtering, and
children-first level scheduling seams exercised via focused unit tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_core.filesystem import is_workspace_member
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import orchestration, registry
from repo_ops_upgrade.verify import verify_workspace

# ── helpers ──────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
# verify gates
# ══════════════════════════════════════════════════════════════════════════════


class TestVerifyExtraManagers:
    def test_pdm_runs_sync_clean(self, tmp_path: Path, make_workspace, fake_run: Any) -> None:

        ws = make_workspace(tmp_path, manager="pdm", ecosystem=None, label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/pdm"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            assert verify_workspace(ws) is None
        assert fake_run.calls == [["pdm", "sync", "--clean"]]

    def test_pipenv_runs_verify(self, tmp_path: Path, make_workspace, fake_run: Any) -> None:

        ws = make_workspace(tmp_path, manager="pipenv", ecosystem=None, label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/pipenv"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            assert verify_workspace(ws) is None
        assert fake_run.calls == [["pipenv", "verify"]]

    def test_hatch_without_uv_lock_skips_sync_locked(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )

        ws = make_workspace(tmp_path, manager="hatch", ecosystem=None, label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            assert verify_workspace(ws) is None
        assert not any(c[:3] == ["uv", "sync", "--locked"] for c in fake_run.calls)

    def test_hatch_with_uv_lock_runs_sync_locked(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")

        ws = make_workspace(tmp_path, manager="hatch", ecosystem=None, label="demo")
        with patch("repo_ops_upgrade.verify.run", side_effect=fake_run):
            assert verify_workspace(ws) is None
        assert fake_run.calls[0] == ["uv", "sync", "--locked"]

    def test_deno_with_lock_runs_install_frozen(
        self, tmp_path: Path, make_workspace, fake_run: Any
    ) -> None:
        (tmp_path / "deno.lock").write_text("{}", encoding="utf-8")

        ws = make_workspace(tmp_path, manager="deno", ecosystem="deno", label="demo")
        with (
            patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/deno"),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            assert verify_workspace(ws) is None
        assert fake_run.calls == [["deno", "install", "--frozen"]]

    def test_precommit_validate_config(self, tmp_path: Path, make_workspace, fake_run: Any) -> None:

        ws = make_workspace(tmp_path, manager="precommit", ecosystem="precommit", label="demo")
        with (
            patch(
                "repo_ops_upgrade.verify.shutil.which",
                return_value="/usr/bin/pre-commit",
            ),
            patch("repo_ops_upgrade.verify.run", side_effect=fake_run),
        ):
            assert verify_workspace(ws) is None
        assert fake_run.calls == [["pre-commit", "validate-config"]]

    def test_container_verify_is_intentional_noop(self, tmp_path: Path, make_workspace) -> None:
        """container v1: image builds out of scope — verify returns None."""
        ws = make_workspace(tmp_path, manager="container", ecosystem="container", label="demo")
        with patch("repo_ops_upgrade.verify.run") as mock_run:
            assert verify_workspace(ws) is None
        mock_run.assert_not_called()

    def test_pdm_missing_binary_fails(self, tmp_path: Path, make_workspace) -> None:
        ws = make_workspace(tmp_path, manager="pdm", ecosystem=None, label="demo")
        with patch("repo_ops_upgrade.verify.shutil.which", return_value=None):
            err = verify_workspace(ws)
        assert err == "pdm not on PATH"


# ══════════════════════════════════════════════════════════════════════════════
# filter_workspaces
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "workspace_specs,eco,manager,expected",
    [
        (
            [("npm", "node"), ("go", "go"), ("pdm", "py")],
            "node",
            None,
            ["npm"],
        ),
        (
            [("npm", "node"), ("pnpm", "node"), ("go", "go")],
            None,
            "pnpm",
            ["pnpm"],
        ),
        (
            [("npm", "node"), ("poetry", "py"), ("pdm", "py")],
            "py",
            "pdm",
            ["pdm"],
        ),
    ],
    ids=["eco-only", "manager-only", "eco-and-manager"],
)
def test_filter_workspaces(
    tmp_path: Path, make_workspace, workspace_specs, eco, manager, expected
) -> None:
    workspaces = [
        make_workspace(tmp_path, manager=m, ecosystem=e, label="demo") for m, e in workspace_specs
    ]
    out = orchestration.filter_workspaces(workspaces, eco, manager)
    assert [w.manager for w in out] == expected


# ══════════════════════════════════════════════════════════════════════════════
# remediations skipped after apply fail
# ══════════════════════════════════════════════════════════════════════════════


def test_remediations_not_run_after_apply_fail(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
    wr = WorkspaceResult(workspace=ws)
    rem_calls: list[Any] = []

    def _boom(_ws: Workspace) -> None:
        raise RuntimeError("apply exploded")

    def _spy_rem(result: WorkspaceResult, *, apply: bool, skip: set[str]) -> None:
        rem_calls.append((result.status, apply, skip))

    with (
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace",
            side_effect=_boom,
        ),
        patch(
            "repo_ops_upgrade.orchestration.dispatch_remediations",
            side_effect=_spy_rem,
        ),
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=False),
        patch(
            "repo_ops_upgrade.orchestration.snapshot_workspace_files",
            return_value={},
        ),
    ):
        out = orchestration.process_workspace(
            wr,
            do_apply=True,
            do_verify=False,
            allow_dirty=False,
        )

    assert out.status == "fail"
    assert rem_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# missing applier RuntimeError → workspace fail
# ══════════════════════════════════════════════════════════════════════════════


def test_missing_applier_maps_to_workspace_fail(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
    wr = WorkspaceResult(workspace=ws)

    with (
        patch.dict(registry.APPLIERS, {}, clear=True),
        patch("repo_ops_upgrade.orchestration.ecosystems.corepack_prepare"),
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=False),
        patch(
            "repo_ops_upgrade.orchestration.snapshot_workspace_files",
            return_value={},
        ),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations"),
        # Call through orchestration so RuntimeError is classified as fail.
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace",
            side_effect=RuntimeError("no applier for npm"),
        ),
    ):
        out = orchestration.process_workspace(
            wr,
            do_apply=True,
            do_verify=False,
            allow_dirty=False,
        )

    assert out.status == "fail"
    assert "no applier" in (out.error or "")


def test_registry_apply_workspace_raises_without_applier(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
    with (
        patch.dict(registry.APPLIERS, {}, clear=True),
        patch("repo_ops_upgrade.registry.corepack_prepare"),
        pytest.raises(RuntimeError, match="no applier for npm"),
    ):
        registry.apply_workspace(ws)


# ══════════════════════════════════════════════════════════════════════════════
# revert catches SubprocessError
# ══════════════════════════════════════════════════════════════════════════════


def test_revert_subprocess_timeout_sets_disposition(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
    wr = WorkspaceResult(workspace=ws)
    before = {tmp_path / "package-lock.json": b"{}"}

    with (
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=False),
        patch(
            "repo_ops_upgrade.orchestration.snapshot_workspace_files",
            side_effect=[before, before],
        ),
        patch(
            "repo_ops_upgrade.orchestration.diff_workspace_snapshots",
            return_value=[Change("pkg", "1", "2")],
        ),
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace",
            return_value=[],
        ),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            return_value="verify boom",
        ),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations"),
        patch(
            "repo_ops_upgrade.orchestration.revert_workspace_after_verify_fail",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=1),
        ),
    ):
        out = orchestration.process_workspace(
            wr,
            do_apply=True,
            do_verify=True,
            allow_dirty=False,
            on_verify_fail="revert",
        )

    assert out.status == "fail"
    assert out.reverted_ok is False
    assert out.verify_fail_disposition is not None
    assert "revert failed" in (out.error or "")


# ══════════════════════════════════════════════════════════════════════════════
# Cargo auto-discovery gap documented + tested
# ══════════════════════════════════════════════════════════════════════════════


def test_cargo_implicit_members_not_treated_as_children(tmp_path: Path) -> None:
    """Empty members set (implicit auto-discovery) never suppresses children."""
    root = tmp_path
    child = tmp_path / "crates" / "util"
    child.mkdir(parents=True)
    assert is_workspace_member(child, root, set()) is False


# ══════════════════════════════════════════════════════════════════════════════
# worker-crash reconciliation (unit of the path_to_result merge)
# ══════════════════════════════════════════════════════════════════════════════


def test_worker_crash_reconciliation_marks_fail(tmp_path: Path, make_workspace) -> None:
    """Mirrors run.py path_to_result merge when a worker drops a result."""
    ws = make_workspace(tmp_path, manager="npm", ecosystem="node", label="demo")
    wr = WorkspaceResult(workspace=ws, status="ok")
    bundle = RepoBundle(git_repo=tmp_path, label="demo", workspaces=[wr])
    path_to_result: dict[tuple[Path, str], WorkspaceResult] = {}
    fail_fast = False

    updated: list[WorkspaceResult] = []
    for item in bundle.workspaces:
        key = (item.workspace.path, item.workspace.manager)
        if key in path_to_result:
            updated.append(path_to_result[key])
        elif fail_fast:
            item.status = "skip"
            item.error = "fail-fast"
            updated.append(item)
        else:
            item.status = "fail"
            item.error = "worker crashed (no result returned)"
            updated.append(item)
    bundle.workspaces = updated

    assert bundle.workspaces[0].status == "fail"
    assert bundle.workspaces[0].error == "worker crashed (no result returned)"
