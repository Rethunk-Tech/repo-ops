"""Integration tests for resume/checkpoint correctness.

These tests drive the real ``run_upgrade`` → ``_run_upgrade_body`` →
``post_apply_commit`` pipeline end to end (real git repos, real commits,
real checkpoint file on disk) — only the ecosystem-specific apply step
(``orchestration.process_workspace``) is stubbed, since what it does to a
given manifest is not what these tests are protecting.

Key invariants:
  1. No checkpoint is written during the apply phase alone (before commit).
     A run with no commit phase leaves no entry, so the next resume
     re-applies the repo rather than silently skipping it.
  2. A checkpoint IS written after the commit phase, keyed on the post-commit
     HEAD SHA.  A subsequent resume skips that repo.
  3. A dirty-skipped repo (status="skip") is NEVER written to the checkpoint.
  4. A resume with a different scope fingerprint (different --filter or
     --manager) discards the stale checkpoint and re-processes all repos
     rather than false-skipping them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import repo_ops_core.state as state_mod
import repo_ops_upgrade.run as run_mod
from repo_ops_models import Change, RepoBundle, Workspace, WorkspaceResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bundle(git_repo: Path) -> RepoBundle:
    """Minimal single-workspace RepoBundle rooted at *git_repo*."""
    ws = Workspace(
        path=git_repo,
        ecosystem="node",
        manager="npm",
        git_repo=git_repo,
        label=git_repo.name,
    )
    return RepoBundle(
        git_repo=git_repo,
        label=git_repo.name,
        workspaces=[WorkspaceResult(workspace=ws)],
    )


def _bump_processor(calls: list[Path]):
    """Fake ``orchestration.process_workspace``: a real, on-disk manifest bump."""

    def _process(wr: WorkspaceResult, **_kwargs: object) -> WorkspaceResult:
        calls.append(wr.workspace.git_repo)
        manifest = wr.workspace.git_repo / "package.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["version"] = f"{data.get('version', '0.0.0')}-bump{len(calls)}"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        wr.status = "ok"
        wr.changes = [Change(name="demo-dep", from_ver="1.0.0", to="1.0.1")]
        return wr

    return _process


def _dirty_skip_processor(calls: list[Path]):
    """Fake ``orchestration.process_workspace``: apply skips, nothing touched on disk."""

    def _process(wr: WorkspaceResult, **_kwargs: object) -> WorkspaceResult:
        calls.append(wr.workspace.git_repo)
        wr.status = "skip"
        wr.error = "dirty worktree"
        return wr

    return _process


def _run_round(
    monkeypatch: pytest.MonkeyPatch,
    git_repo: Path,
    process_fn,
    *,
    commit: str = "conventional",
    eco_filter: str | None = None,
) -> int:
    """Drive one real apply(+commit) round of run_upgrade over *git_repo*.

    Discovery is stubbed to hand back exactly one bundle for *git_repo* so
    the test controls scope precisely; everything downstream of that
    (filtering, apply dispatch, checkpoint, commit) is the real code.
    """
    bundle = _make_bundle(git_repo)
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "discover_all",
        lambda *a, **k: [wr.workspace for wr in bundle.workspaces],
    )
    monkeypatch.setattr(
        run_mod.workspace_discovery,
        "group_by_repo",
        lambda found, roots: {git_repo: bundle},
    )
    monkeypatch.setattr(run_mod.orchestration, "process_workspace", process_fn)

    argv = ["--flat", "--skip-actions", "--quiet", "--resume", "--commit", commit]
    if eco_filter:
        argv += ["--filter", eco_filter]
    argv.append(str(git_repo))
    return run_mod.run_upgrade(apply=True, argv=argv)


# ---------------------------------------------------------------------------
# Test 1: no checkpoint written when the commit phase never runs
# Invariant: an apply-only run (no --commit) leaves no checkpoint entry, so
# the next resume re-applies the repo rather than false-skipping it.
# ---------------------------------------------------------------------------


def test_no_checkpoint_written_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    calls: list[Path] = []

    rc1 = _run_round(monkeypatch, git_repo, _bump_processor(calls), commit="none")
    assert rc1 == 0
    assert calls == [git_repo], "apply must have run despite no prior checkpoint"
    assert str(git_repo) not in state_mod.load_checkpoint(), (
        "no checkpoint entry may exist when the commit phase never ran"
    )

    rc2 = _run_round(monkeypatch, git_repo, _bump_processor(calls), commit="conventional")
    assert rc2 == 0
    assert calls == [git_repo, git_repo], (
        "repo must be re-applied on resume when no checkpoint exists"
    )
    assert str(git_repo) in state_mod.load_checkpoint(), (
        "the second round's real commit must now be checkpointed"
    )


# ---------------------------------------------------------------------------
# Test 2: checkpoint written after commit; resume skips the committed repo
# ---------------------------------------------------------------------------


def test_checkpoint_written_after_commit_and_resume_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    calls: list[Path] = []

    rc1 = _run_round(monkeypatch, git_repo, _bump_processor(calls))
    assert rc1 == 0
    assert calls == [git_repo]
    checkpoint = state_mod.load_checkpoint()
    assert checkpoint.get(str(git_repo)) == run_mod._get_head_sha(git_repo), (
        "checkpoint must record the real post-commit HEAD SHA"
    )

    rc2 = _run_round(monkeypatch, git_repo, _bump_processor(calls))
    assert rc2 == 0
    assert calls == [git_repo], "repo with committed SHA in checkpoint must be skipped on resume"


# ---------------------------------------------------------------------------
# Test 3: dirty-skipped repo is NOT checkpointed
# ---------------------------------------------------------------------------


def test_dirty_skipped_repo_not_checkpointed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    calls: list[Path] = []

    rc1 = _run_round(monkeypatch, git_repo, _dirty_skip_processor(calls))
    assert rc1 == 0
    assert calls == [git_repo]
    assert str(git_repo) not in state_mod.load_checkpoint(), (
        "a skipped (uncommitted) repo must never be written to the checkpoint"
    )

    rc2 = _run_round(monkeypatch, git_repo, _dirty_skip_processor(calls))
    assert rc2 == 0
    assert calls == [git_repo, git_repo], (
        "a repo absent from the checkpoint must be re-processed on resume"
    )


# ---------------------------------------------------------------------------
# Test 4: differently-scoped resume does NOT false-skip
# ---------------------------------------------------------------------------


def test_different_scope_discards_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git_repo
) -> None:
    git_repo = tmp_path / "repo"
    init_git_repo(git_repo, commit=True, filename="package.json", content='{"version": "0.0.0"}')
    calls: list[Path] = []

    rc1 = _run_round(monkeypatch, git_repo, _bump_processor(calls), eco_filter="node")
    assert rc1 == 0
    assert calls == [git_repo]
    checkpoint = state_mod.load_checkpoint()
    assert str(git_repo) in checkpoint
    assert checkpoint.get("_scope") == "eco=node"

    # Unfiltered resume: scope "" != "eco=node", so the checkpoint must be
    # discarded rather than used to skip the repo.
    rc2 = _run_round(monkeypatch, git_repo, _bump_processor(calls))
    assert rc2 == 0
    assert calls == [git_repo, git_repo], (
        "a checkpoint from a differently-scoped run must not skip this repo"
    )


# ---------------------------------------------------------------------------
# Test 5: scope_fingerprint helper
# ---------------------------------------------------------------------------


def test_scope_fingerprint_empty_for_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unfiltered run produces an empty scope fingerprint."""
    import argparse

    args = argparse.Namespace(eco_filter=None, manager_filter=None)
    assert run_mod._scope_fingerprint(args) == ""


def test_scope_fingerprint_eco_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """--filter=node produces eco=node scope."""
    import argparse

    args = argparse.Namespace(eco_filter="node", manager_filter=None)
    fp = run_mod._scope_fingerprint(args)
    assert "eco=node" in fp


def test_scope_fingerprint_both_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both --filter and --manager in the fingerprint."""
    import argparse

    args = argparse.Namespace(eco_filter="py", manager_filter="uv")
    fp = run_mod._scope_fingerprint(args)
    assert "eco=py" in fp
    assert "mgr=uv" in fp


# ---------------------------------------------------------------------------
# Test 6: --resume CLI flag is registered and accepted
# ---------------------------------------------------------------------------


def test_resume_cli_flag_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """--resume is a registered apply-only flag that does not error."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=True)
    # parse_known_args so we don't need real roots
    args, _ = parser.parse_known_args(["--resume"])
    assert args.resume is True


def test_resume_cli_flag_not_on_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """--resume is NOT registered on the plan parser (apply-only flag)."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    # --resume on plan should be unrecognised
    _, remainder = parser.parse_known_args(["--resume"])
    assert "--resume" in remainder
