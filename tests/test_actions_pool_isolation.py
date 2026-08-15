"""Actions pool worker isolation: per-worker exceptions become FAILED entries.

Task 2 regression guard: a non-WorkflowParseError (e.g. OSError writing a
workflow) must surface as a FAILED ActionBump on the bundle rather than
propagating through the ThreadPoolExecutor and tearing down the whole run
AFTER lockfiles were already mutated on disk (#TASK-2).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from repo_ops_models import RepoBundle
from repo_ops_upgrade import actions as actions_mod
from repo_ops_upgrade import discovery as disc_mod
from repo_ops_upgrade import orchestration as orch_mod
from repo_ops_upgrade import run as run_mod


def _fake_args(**overrides) -> MagicMock:
    """Minimal argparse.Namespace stand-in for _run_upgrade_body."""
    defaults: dict = {
        # Selection
        "skip_actions": False,
        "flat": False,
        "eco_filter": None,
        "manager_filter": None,
        "exclude_patterns": None,
        "submodules": False,
        "submodule_order": "discovery",
        # Upgrade policy
        "conservative": False,
        "migrate_to_uv_lock": False,
        "commit_umbrella": False,
        "actions_org_policy": False,
        "actions_policy_level": None,
        "go_upgrade_mode": None,
        "actions_unpin_sha": False,
        "actions_tag_style": "major",
        "actions_upgrade_mode": None,
        "actions_lint_level": None,
        # Safety
        "allow_dirty": False,
        "strict_exit": False,
        # Output
        "json": False,
        "json_report_path": None,
        "compact": False,
        "quiet": True,
        "top": 10,
        "max_failures": 20,
        "only_outdated": False,
        # Execution
        "fail_fast": False,
        "jobs": 1,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_actions_worker_oserror_becomes_fail_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError from scan_actions → FAILED ActionBump on bundle; run exits 1, not raises."""
    bundle = RepoBundle(git_repo=tmp_path, label="test/repo", workspaces=[], actions=[])
    bundles: dict[Path, RepoBundle] = {tmp_path: bundle}

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        actions_mod,
        "scan_actions",
        MagicMock(side_effect=OSError("simulated disk error")),
    )

    # Must NOT raise; exit code must be 1 (fail entry → actionable work → plan exit 1).
    rc = run_mod._run_upgrade_body(
        apply=False,
        args=_fake_args(),
        roots=[str(tmp_path)],
    )
    assert rc == 1, "expected exit 1 from fail entry, not a raised exception"
    assert len(bundle.actions) == 1
    fail_entry = bundle.actions[0]
    assert fail_entry.status == "fail"
    assert "simulated disk error" in (fail_entry.detail or "")


def test_actions_worker_exception_preserves_other_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When one worker raises, other bundles' actions are still populated."""
    dir_a = tmp_path / "repo_a"
    dir_a.mkdir()
    dir_b = tmp_path / "repo_b"
    dir_b.mkdir()

    bundle_a = RepoBundle(git_repo=dir_a, label="a", workspaces=[], actions=[])
    bundle_b = RepoBundle(git_repo=dir_b, label="b", workspaces=[], actions=[])
    bundles: dict[Path, RepoBundle] = {dir_a: bundle_a, dir_b: bundle_b}

    ok_bump = MagicMock()
    ok_bump.status = "ok"
    ok_bump.detail = "report only"
    ok_bump.pin = "v4"
    ok_bump.latest = "v4"

    def _scan_or_raise(repo: Path, **kw):
        if repo == dir_a:
            raise OSError("disk error on repo_a")
        return [ok_bump], 1

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(actions_mod, "scan_actions", _scan_or_raise)

    rc = run_mod._run_upgrade_body(
        apply=False,
        args=_fake_args(),
        roots=[str(tmp_path)],
    )
    assert rc == 1
    # repo_a: fail entry from the exception
    assert len(bundle_a.actions) == 1
    assert bundle_a.actions[0].status == "fail"
    # repo_b: normal result still populated
    assert bundle_b.actions == [ok_bump]


def test_actions_worker_keyboardinterrupt_still_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KeyboardInterrupt is NOT swallowed — only Exception subclasses are isolated."""
    bundle = RepoBundle(git_repo=tmp_path, label="test", workspaces=[], actions=[])
    bundles = {tmp_path: bundle}

    monkeypatch.setattr(disc_mod, "discover_all", lambda *a, **kw: [])
    monkeypatch.setattr(orch_mod, "filter_workspaces", lambda found, *a, **kw: found)
    monkeypatch.setattr(disc_mod, "group_by_repo", lambda *a, **kw: bundles)
    monkeypatch.setattr(disc_mod, "merge_action_only_bundles", lambda b, *a, **kw: b)
    monkeypatch.setattr(
        actions_mod,
        "scan_actions",
        MagicMock(side_effect=KeyboardInterrupt()),
    )

    # KeyboardInterrupt is NOT an Exception; the pool re-raises it as a
    # concurrent.futures internal wrapping (depends on implementation), but the
    # run must NOT silently eat it.
    with pytest.raises((KeyboardInterrupt, Exception)):
        run_mod._run_upgrade_body(
            apply=False,
            args=_fake_args(),
            roots=[str(tmp_path)],
        )
