"""Umbrella submodule pointer-bump commits (REPO_OPS_COMMIT_UMBRELLA=1)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest

from repo_ops_upgrade.post_apply_commit import (
    CommitOutcome,
    commit_umbrella_submodule_pointers,
)
from repo_ops_upgrade.submodules import SubmoduleLink


def _write_gitmodules(parent: Path, path: str, url: str) -> None:
    (parent / ".gitmodules").write_text(
        f'[submodule "{path}"]\n\tpath = {path}\n\turl = {url}\n',
        encoding="utf-8",
    )


def _make_child_outcome(
    git_repo: Path,
    status: Literal["committed", "dry_run", "skipped", "failed"] = "committed",
) -> CommitOutcome:
    return CommitOutcome(
        git_repo=git_repo,
        label=git_repo.name,
        status=status,
        message="chore(deps): update",
        files=["go.mod"],
    )


# ---------------------------------------------------------------------------
# Env-gate: feature is off by default
# ---------------------------------------------------------------------------


def test_env_off_produces_no_umbrella_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
) -> None:
    """When REPO_OPS_COMMIT_UMBRELLA is absent, no umbrella commit is attempted."""
    monkeypatch.delenv("REPO_OPS_COMMIT_UMBRELLA", raising=False)

    umbrella = tmp_path / "umbrella"
    child = umbrella / "child"
    child.mkdir(parents=True)
    init_git_repo(umbrella)
    init_git_repo(child)

    links = [
        SubmoduleLink(
            path="child",
            git_repo=child.resolve(),
            parent=umbrella.resolve(),
        )
    ]
    child_outcomes = [_make_child_outcome(child.resolve())]

    outcomes, code = commit_umbrella_submodule_pointers(
        links,
        child_outcomes,
        mode="conventional",
        dry_run=False,
        quiet=True,
    )
    assert outcomes == []
    assert code == 0


# ---------------------------------------------------------------------------
# No child commits → no umbrella commit
# ---------------------------------------------------------------------------


def test_no_child_commits_produces_no_umbrella_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
) -> None:
    """When no child received a commit (all skipped), no umbrella commit runs."""
    monkeypatch.setenv("REPO_OPS_COMMIT_UMBRELLA", "1")

    umbrella = tmp_path / "umbrella"
    child = umbrella / "child"
    child.mkdir(parents=True)
    init_git_repo(umbrella)
    init_git_repo(child)

    links = [
        SubmoduleLink(
            path="child",
            git_repo=child.resolve(),
            parent=umbrella.resolve(),
        )
    ]
    # All children skipped — no commits this run.
    child_outcomes = [
        CommitOutcome(
            git_repo=child.resolve(),
            label="child",
            status="skipped",
            detail="no changes",
        )
    ]

    outcomes, code = commit_umbrella_submodule_pointers(
        links,
        child_outcomes,
        mode="conventional",
        dry_run=False,
        quiet=True,
    )
    assert outcomes == []
    assert code == 0


# ---------------------------------------------------------------------------
# Core scenario: 2 committed children + 1 uncommitted + 1 unrelated dirty file
# ---------------------------------------------------------------------------


def test_stages_only_committed_child_gitlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
) -> None:
    """Stages exactly the 2 gitlink paths for committed children; unrelated dirty
    files and uncommitted-child gitlinks are never touched."""
    monkeypatch.setenv("REPO_OPS_COMMIT_UMBRELLA", "1")

    umbrella = tmp_path / "umbrella"
    child_a = umbrella / "child-a"
    child_b = umbrella / "child-b"
    child_c = umbrella / "child-c"
    for d in (child_a, child_b, child_c):
        d.mkdir(parents=True)
        init_git_repo(d)
    init_git_repo(umbrella)
    # Also plant an unrelated dirty file in the umbrella.
    (umbrella / "README.md").write_text("dirty\n", encoding="utf-8")

    links = [
        SubmoduleLink(path="child-a", git_repo=child_a.resolve(), parent=umbrella.resolve()),
        SubmoduleLink(path="child-b", git_repo=child_b.resolve(), parent=umbrella.resolve()),
        SubmoduleLink(path="child-c", git_repo=child_c.resolve(), parent=umbrella.resolve()),
    ]

    # child-a and child-b received commits; child-c was skipped.
    child_outcomes = [
        _make_child_outcome(child_a.resolve(), status="committed"),
        _make_child_outcome(child_b.resolve(), status="committed"),
        CommitOutcome(
            git_repo=child_c.resolve(),
            label="child-c",
            status="skipped",
            detail="no changes",
        ),
    ]

    # Simulate porcelain output: child-a and child-b are dirty gitlinks,
    # child-c is dirty too (but not committed), plus an unrelated file.
    fake_porcelain = " M child-a\0 M child-b\0 M child-c\0 M README.md\0"

    staged: list[str] = []

    from subprocess import CompletedProcess

    def _fake_run_git(repo: Path, *args: str, timeout: int = 60) -> CompletedProcess[str]:
        cmd_list = list(args)
        if "status" in cmd_list and "--porcelain" in cmd_list:
            return CompletedProcess(cmd_list, 0, stdout=fake_porcelain, stderr="")
        if "add" in cmd_list and "--" in cmd_list:
            idx = cmd_list.index("--")
            staged.append(cmd_list[idx + 1])
            return CompletedProcess(cmd_list, 0, stdout="", stderr="")
        if "commit" in cmd_list:
            return CompletedProcess(
                cmd_list,
                0,
                stdout="[main abc1234] chore(submodules): bump 2 child pin(s)",
                stderr="",
            )
        return CompletedProcess(cmd_list, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply_commit.run_git", side_effect=_fake_run_git):
        outcomes, code = commit_umbrella_submodule_pointers(
            links,
            child_outcomes,
            mode="conventional",
            dry_run=False,
            quiet=True,
        )

    assert code == 0
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status == "committed"
    assert set(outcome.files) == {"child-a", "child-b"}
    # child-c and README.md must never be staged.
    assert "child-c" not in staged
    assert "README.md" not in staged


# ---------------------------------------------------------------------------
# Commit message shape
# ---------------------------------------------------------------------------


def test_umbrella_commit_message_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
) -> None:
    """Commit subject is 'chore(submodules): bump N child pin(s)'; body names children."""
    monkeypatch.setenv("REPO_OPS_COMMIT_UMBRELLA", "1")

    umbrella = tmp_path / "umbrella"
    child_x = umbrella / "libx"
    child_x.mkdir(parents=True)
    init_git_repo(umbrella)
    init_git_repo(child_x)

    links = [SubmoduleLink(path="libx", git_repo=child_x.resolve(), parent=umbrella.resolve())]
    child_outcomes = [_make_child_outcome(child_x.resolve())]

    fake_porcelain = " M libx\0"

    from subprocess import CompletedProcess

    committed_message: list[str] = []

    def _fake_run_git(repo: Path, *args: str, timeout: int = 60) -> CompletedProcess[str]:
        cmd_list = list(args)
        if "status" in cmd_list and "--porcelain" in cmd_list:
            return CompletedProcess(cmd_list, 0, stdout=fake_porcelain, stderr="")
        if "add" in cmd_list:
            return CompletedProcess(cmd_list, 0, stdout="", stderr="")
        if "commit" in cmd_list and "-m" in cmd_list:
            idx = cmd_list.index("-m")
            committed_message.append(cmd_list[idx + 1])
            return CompletedProcess(
                cmd_list,
                0,
                stdout="[main abc1234] chore(submodules): bump 1 child pin(s)",
                stderr="",
            )
        return CompletedProcess(cmd_list, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply_commit.run_git", side_effect=_fake_run_git):
        outcomes, code = commit_umbrella_submodule_pointers(
            links,
            child_outcomes,
            mode="conventional",
            dry_run=False,
            quiet=True,
        )

    assert code == 0
    assert len(outcomes) == 1
    msg = committed_message[0]
    assert msg.startswith("chore(submodules): bump 1 child pin(s)")
    # Body must name the child.
    assert "libx" in msg


# ---------------------------------------------------------------------------
# Dry-run: no git commands, correct outcome status
# ---------------------------------------------------------------------------


def test_dry_run_produces_no_git_add(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    init_git_repo,
) -> None:
    """In dry-run mode, outcome is 'dry_run' and no git add/commit is executed."""
    monkeypatch.setenv("REPO_OPS_COMMIT_UMBRELLA", "1")

    umbrella = tmp_path / "umbrella"
    child_y = umbrella / "child-y"
    child_y.mkdir(parents=True)
    init_git_repo(umbrella)
    init_git_repo(child_y)

    links = [SubmoduleLink(path="child-y", git_repo=child_y.resolve(), parent=umbrella.resolve())]
    child_outcomes = [_make_child_outcome(child_y.resolve())]

    fake_porcelain = " M child-y\0"
    git_calls: list[tuple[str, ...]] = []

    from subprocess import CompletedProcess

    def _fake_run_git(repo: Path, *args: str, timeout: int = 60) -> CompletedProcess[str]:
        git_calls.append(args)
        cmd_list = list(args)
        if "status" in cmd_list and "--porcelain" in cmd_list:
            return CompletedProcess(cmd_list, 0, stdout=fake_porcelain, stderr="")
        return CompletedProcess(cmd_list, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply_commit.run_git", side_effect=_fake_run_git):
        outcomes, code = commit_umbrella_submodule_pointers(
            links,
            child_outcomes,
            mode="conventional",
            dry_run=True,
            quiet=True,
        )

    assert code == 0
    assert len(outcomes) == 1
    assert outcomes[0].status == "dry_run"
    assert outcomes[0].files == ["child-y"]
    # Only git status should have been called; no git add or commit.
    for call in git_calls:
        assert "add" not in call
        assert "commit" not in call
