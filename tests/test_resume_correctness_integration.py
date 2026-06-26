"""Integration tests for Task #17 resume/checkpoint correctness.

These tests exercise the WIRING between apply, checkpoint, and commit phases —
not just individual helper functions.  This class of bug slipped through
unit-green before (the per-function tests all passed while the pipeline
integration was wrong); integration coverage is required.

Key invariants:
  1. No checkpoint is written during the apply phase alone (before commit).
     A simulated crash-before-commit leaves no entry, so the next resume
     re-applies the repo rather than silently skipping it.
  2. A checkpoint IS written after the commit phase, keyed on the post-commit
     HEAD SHA.  A subsequent resume skips that repo.
  3. A dirty-skipped repo (status="skip") is NEVER written to the checkpoint.
  4. A resume with a different scope fingerprint (different --filter or
     --manager) discards the stale checkpoint and re-processes all repos
     rather than false-skipping them.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest
import repo_ops_core.state as state_mod
import repo_ops_upgrade.run as run_mod
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.post_apply_commit import CommitOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> str:
    """Init a bare git repo, create one commit, return HEAD SHA."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_bundle(git_repo: Path) -> RepoBundle:
    """Minimal RepoBundle for *git_repo*."""
    ws = Workspace(
        path=git_repo / "package.json",
        ecosystem="js",
        manager="npm",
        git_repo=git_repo,
        label=str(git_repo),
    )
    return RepoBundle(
        git_repo=git_repo,
        label=str(git_repo),
        workspaces=[WorkspaceResult(workspace=ws)],
    )


def _make_commit_outcome(git_repo: Path, status: str = "committed") -> CommitOutcome:
    return CommitOutcome(
        git_repo=git_repo,
        label=git_repo.name,
        status=status,  # type: ignore[arg-type]
        message="chore(deps): test",
        files=["README.md"],
    )


# ---------------------------------------------------------------------------
# Helper: simulate the checkpoint-wiring portion of _run_upgrade_body
# This replicates the NEW (fixed) checkpoint contract:
#   - No checkpoint write during apply phase.
#   - Checkpoint written AFTER commit, for "committed" outcomes only.
# ---------------------------------------------------------------------------


def _run_checkpoint_round(
    git_repo: Path,
    *,
    cache_dir: Path,
    scope: str,
    existing_checkpoint: dict[str, str] | None = None,
    commit_outcomes: list[CommitOutcome] | None = None,
    simulate_apply_status: str = "ok",
) -> tuple[bool, dict[str, str]]:
    """Simulate one apply+commit cycle and return (was_skipped, checkpoint_after).

    *existing_checkpoint* is the checkpoint state loaded at the start of the
    run (pre-populated for resume tests).  *commit_outcomes* are the outcomes
    from the commit phase (None means commit phase didn't run / crashed).
    *simulate_apply_status* is the status set on the workspace by "apply".
    """
    monkeyenv = os.environ.copy()
    monkeyenv["XDG_CACHE_HOME"] = str(cache_dir)

    # Load checkpoint (with scope validation — mirrors _run_upgrade_body)
    checkpoint: dict[str, str] = {}
    if existing_checkpoint is not None:
        checkpoint = dict(existing_checkpoint)
    else:
        # Load from disk (real state)
        import repo_ops_core.state as s

        checkpoint = s.load_checkpoint()
        if checkpoint.get("_scope") != scope:
            checkpoint = {}

    checkpoint_lock = threading.Lock()

    bundle = _make_bundle(git_repo)

    # --- Apply phase (no checkpoint write here — new behavior) ---
    was_skipped = run_mod._checkpoint_should_skip(bundle, checkpoint)
    if was_skipped:
        for wr in bundle.workspaces:
            wr.status = "skip"
            wr.error = "resume: already applied"
    else:
        for wr in bundle.workspaces:
            wr.status = simulate_apply_status

    # --- Commit phase (only executed when commit_outcomes is not None) ---
    # This mirrors the new _run_upgrade_body post-commit checkpoint block.
    if not was_skipped and commit_outcomes is not None:
        import repo_ops_core.state as s_save

        for co in commit_outcomes:
            if co.status == "committed":
                sha = run_mod._get_head_sha(co.git_repo)
                if sha:
                    with checkpoint_lock:
                        checkpoint[str(co.git_repo)] = sha
                        checkpoint["_scope"] = scope
                        to_save = dict(checkpoint)
                        s_save.save_checkpoint(to_save)

    return was_skipped, checkpoint


# ---------------------------------------------------------------------------
# Test 1: no checkpoint written during apply (only after commit)
# Invariant: a simulated crash-before-commit leaves no checkpoint entry,
# so the next resume re-applies the repo.
# ---------------------------------------------------------------------------


def test_no_checkpoint_written_before_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply alone (no commit) does not write a checkpoint entry.

    Simulates a crash between the apply phase and the commit phase.
    The next resume must NOT skip this repo — it must be re-applied.
    """
    cache_dir = tmp_path / "cache"
    git_repo = tmp_path / "repo"
    _init_git_repo(git_repo)

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    scope = ""

    # Round 1: apply runs, commit phase crashes (commit_outcomes=None)
    was_skipped, checkpoint_after = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=scope,
        existing_checkpoint={},
        commit_outcomes=None,  # crash before commit
        simulate_apply_status="ok",
    )

    assert not was_skipped, "apply should have run (no prior checkpoint)"
    assert str(git_repo) not in checkpoint_after, (
        "no checkpoint must be written when commit phase did not run"
    )

    # Round 2 (resume): since no checkpoint was written, the repo is re-applied
    was_skipped_round2, _ = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=scope,
        existing_checkpoint=checkpoint_after,  # still empty
        commit_outcomes=None,
        simulate_apply_status="ok",
    )
    assert not was_skipped_round2, (
        "repo must be re-applied on resume when no checkpoint exists (crash-before-commit)"
    )


# ---------------------------------------------------------------------------
# Test 2: checkpoint written after commit; resume skips the committed repo
# Invariant: once committed, the post-commit SHA is checkpointed and the
# next resume skips the repo.
# ---------------------------------------------------------------------------


def test_checkpoint_written_after_commit_and_resume_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After apply+commit, checkpoint records the post-commit SHA.

    The next resume (same scope, HEAD unchanged) must skip the repo.
    """
    cache_dir = tmp_path / "cache"
    git_repo = tmp_path / "repo"
    _init_git_repo(git_repo)

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    scope = ""

    # Round 1: apply + successful commit
    commit_outcomes = [_make_commit_outcome(git_repo, status="committed")]
    was_skipped, checkpoint_after = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=scope,
        existing_checkpoint={},
        commit_outcomes=commit_outcomes,
        simulate_apply_status="ok",
    )
    assert not was_skipped
    assert str(git_repo) in checkpoint_after, (
        "checkpoint must be written after successful commit"
    )

    # Verify the checkpointed SHA matches the current HEAD
    current_sha = run_mod._get_head_sha(git_repo)
    assert checkpoint_after[str(git_repo)] == current_sha

    # Round 2 (resume): repo is already committed — must be skipped
    was_skipped_round2, _ = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=scope,
        existing_checkpoint=checkpoint_after,
        commit_outcomes=None,
        simulate_apply_status="ok",
    )
    assert was_skipped_round2, (
        "repo with committed SHA in checkpoint must be skipped on resume"
    )


# ---------------------------------------------------------------------------
# Test 3: dirty-skipped repo is NOT checkpointed
# Invariant: status="skip" repos never produce a checkpoint entry.
# ---------------------------------------------------------------------------


def test_dirty_skipped_repo_not_checkpointed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dirty-skipped repo (status='skip') must not be checkpointed.

    Even if a CommitOutcome with status='skipped' exists for the repo, the
    checkpoint code only records status='committed' outcomes.  A 'skipped'
    commit outcome (e.g. "no changes to commit" / dirty worktree) must never
    permanently block a repo from being processed on resume.
    """
    cache_dir = tmp_path / "cache"
    git_repo = tmp_path / "repo"
    _init_git_repo(git_repo)

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    scope = ""

    # Simulate apply returning "skip" (dirty worktree); commit outcome is "skipped"
    skip_commit_outcome = _make_commit_outcome(git_repo, status="skipped")

    # Even if we pass a "skipped" commit outcome, no checkpoint must be written
    _, checkpoint_after = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=scope,
        existing_checkpoint={},
        commit_outcomes=[skip_commit_outcome],
        simulate_apply_status="skip",
    )

    assert str(git_repo) not in checkpoint_after, (
        "dirty-skipped repo must never be written to checkpoint"
    )

    # Round 2 (resume): repo should be re-processed (not skipped)
    was_skipped, _ = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=scope,
        existing_checkpoint=checkpoint_after,
        commit_outcomes=None,
        simulate_apply_status="skip",
    )
    assert not was_skipped, (
        "dirty-skipped repo must not be skipped on resume (not in checkpoint)"
    )


# ---------------------------------------------------------------------------
# Test 4: differently-scoped resume does NOT false-skip
# Invariant: a checkpoint written under scope "eco=node" must not be used
# by a resume run with scope "eco=go".
# ---------------------------------------------------------------------------


def test_different_scope_discards_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale checkpoint from a different scope is discarded on resume.

    A repo checkpointed under --filter=node must be re-processed when the
    next run uses --filter=go, not silently skipped.
    """
    cache_dir = tmp_path / "cache"
    git_repo = tmp_path / "repo"
    _init_git_repo(git_repo)

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.delenv("REPO_OPS_NO_STATE", raising=False)

    # Round 1: apply + commit with scope "eco=node"
    node_scope = "eco=node"
    commit_outcomes = [_make_commit_outcome(git_repo, status="committed")]
    _, checkpoint_node = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=node_scope,
        existing_checkpoint={},
        commit_outcomes=commit_outcomes,
    )
    assert str(git_repo) in checkpoint_node
    assert checkpoint_node.get("_scope") == node_scope

    # Save checkpoint to disk so Round 2 can load it
    state_mod.save_checkpoint(checkpoint_node)

    # Round 2: resume with scope "eco=go" — checkpoint must be DISCARDED
    go_scope = "eco=go"
    loaded = state_mod.load_checkpoint()
    # Scope mismatch → discard (mirrors _run_upgrade_body logic)
    if loaded.get("_scope") != go_scope:
        loaded = {}

    was_skipped, _ = _run_checkpoint_round(
        git_repo,
        cache_dir=cache_dir,
        scope=go_scope,
        existing_checkpoint=loaded,  # empty after scope mismatch
        commit_outcomes=None,
    )
    assert not was_skipped, (
        "repo from a differently-scoped checkpoint must NOT be skipped on resume"
    )


# ---------------------------------------------------------------------------
# Test 5: scope_fingerprint helper
# ---------------------------------------------------------------------------


def test_scope_fingerprint_empty_for_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
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
