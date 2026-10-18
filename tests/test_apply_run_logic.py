"""Apply-run decision helpers: actionable work, exit code, sibling rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_ops_models import ActionBump, Change, OwnedEdit, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.post_apply import capture_worktree_state
from repo_ops_upgrade.run import _has_actionable_work, _resolve_exit_code, _rollback_vetoed_repo


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label="demo"
    )


def _bundles(
    tmp_path: Path,
    *,
    wr: WorkspaceResult | None = None,
    actions: list[ActionBump] | None = None,
) -> dict[Path, RepoBundle]:
    return {
        tmp_path: RepoBundle(
            git_repo=tmp_path,
            label="demo",
            workspaces=[] if wr is None else [wr],
            actions=actions or [],
        )
    }


@pytest.mark.parametrize(
    ("wr_kwargs", "actions", "expected"),
    [
        ({}, [], False),
        ({"status": "skip"}, [], False),
        ({"status": "fail"}, [], True),
        ({"changes": [Change("pkg", "1", "2")]}, [], True),
        ({"remediations": [{"hook": "biome", "status": "applied"}]}, [], True),
        (
            {},
            [ActionBump("ci.yml", "actions/checkout", "v3", "v4", status="fail")],
            True,
        ),
        (
            {},
            [
                ActionBump(
                    "ci.yml",
                    "actions/checkout",
                    "v3",
                    "v4",
                    detail="report only: SHA-pinned, not auto-applied",
                )
            ],
            False,
        ),
        (
            {},
            [ActionBump("ci.yml", "actions/checkout", "v3", "v4")],
            True,
        ),
        (
            {},
            [ActionBump("ci.yml", "actions/checkout", "v4", "v4")],
            False,
        ),
    ],
    ids=(
        "clean-ok",
        "skip",
        "workspace-fail",
        "changes",
        "remediations",
        "action-fail",
        "action-report-only",
        "action-pin-behind",
        "action-current",
    ),
)
def test_has_actionable_work(
    tmp_path: Path,
    wr_kwargs: dict,
    actions: list[ActionBump],
    expected: bool,
) -> None:
    wr = WorkspaceResult(workspace=_ws(tmp_path), **wr_kwargs)
    assert _has_actionable_work(_bundles(tmp_path, wr=wr, actions=actions)) is expected


@pytest.mark.parametrize(
    ("status", "apply", "expected"),
    [
        ("ok", True, 0),
        ("ok", False, 0),
        ("fail", True, 1),
        ("fail", False, 1),
        ("changes", False, 1),
        ("changes", True, 0),
    ],
    ids=(
        "apply-clean",
        "plan-clean",
        "apply-fail",
        "plan-fail",
        "plan-actionable",
        "apply-actionable",
    ),
)
def test_resolve_exit_code(tmp_path: Path, status: str, apply: bool, expected: int) -> None:
    kwargs: dict = {}
    if status == "fail":
        kwargs["status"] = "fail"
    elif status == "changes":
        kwargs["changes"] = [Change("pkg", "1", "2")]
    bundles = _bundles(tmp_path, wr=WorkspaceResult(workspace=_ws(tmp_path), **kwargs))
    assert (
        _resolve_exit_code(bundles, apply=apply, has_actionable_work=_has_actionable_work(bundles))
        == expected
    )


@pytest.mark.parametrize(
    ("fail_disposition", "skip_sibling"),
    [
        (None, False),
        ("FAILED_CLEAN", False),
        (None, True),
    ],
    ids=("fail-rolls-siblings", "verify-fail-left-alone", "skip-left-alone"),
)
def test_rollback_vetoed_repo(
    tmp_path: Path,
    init_git_repo,
    fail_disposition: str | None,
    skip_sibling: bool,
) -> None:
    """Passing siblings of a failed workspace revert; skip/verify-fail do not."""
    init_git_repo(tmp_path, commit=True, filename="package-lock.json", content="{}\n")
    lock = tmp_path / "package-lock.json"
    original = lock.read_bytes()
    before = capture_worktree_state(tmp_path)
    mutated = b'{"mutated":true}\n'
    lock.write_bytes(mutated)

    ws = _ws(tmp_path)
    passing = WorkspaceResult(
        workspace=ws,
        status="ok",
        owned_edits={
            "package-lock.json": OwnedEdit(before=original, from_head=True, applied=mutated),
        },
    )
    other_status = "skip" if skip_sibling else "fail"
    other = WorkspaceResult(
        workspace=ws,
        status=other_status,
        verify_fail_disposition=fail_disposition,
        owned_edits={},
    )
    _rollback_vetoed_repo(tmp_path, before, [passing, other])

    assert passing.status == "skip"
    assert passing.error == "reverted: another workspace in this repo failed"
    assert lock.read_bytes() == original
    assert other.status == other_status
