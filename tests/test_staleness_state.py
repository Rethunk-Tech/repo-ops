"""Staleness scoring state: recording, delta, reset, and report surfacing.

Covers the behind-latest staleness store in ``repo_ops_core.state`` and its
read-back through ``repo_ops_upgrade.report``: first-run recording, the
second-run score/delta math, the zero-behind reset (and entry pruning), the
orchestration recording call site, and byte-stability of report output when
the store is empty.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from unittest.mock import patch

from repo_ops_core import state as state_mod
from repo_ops_core.state import cache_get_staleness, cache_record_behind_latest
from repo_ops_models import Change, RepoBundle, WorkspaceResult
from repo_ops_upgrade.orchestration import process_workspace
from repo_ops_upgrade.report import (
    build_json_payload,
    build_run_summary,
    print_run_footer,
)


def _reset_module_cache() -> None:
    """Reset the module-level lazy cache and per-run rotation guard."""
    state_mod._CACHE = None
    state_mod._DIRTY = False
    state_mod._BEHIND_RUN_SEEN.clear()


def _new_run() -> None:
    """Simulate a fresh repo-ops process: persist, drop cache, new rotation."""
    state_mod._flush_cache()
    _reset_module_cache()


def test_first_run_records_and_second_run_scores_delta(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _reset_module_cache()
    repo = tmp_path / "repo"
    d0 = date(2026, 7, 1)

    # Run 1: two workspaces of the same repo accumulate per-manager counts.
    cache_record_behind_latest(repo, "npm", 2, d0)
    cache_record_behind_latest(repo, "go", 1, d0)
    info = cache_get_staleness(repo, d0)
    assert info == {
        "total_behind": 3,
        "by_manager": {"npm": 2, "go": 1},
        "first_behind": "2026-07-01",
        "runs": 1,
        "staleness_score": 0.0,  # day 0: 3 behind * 0 days
        "new_behind_since_last_run": 3,  # nothing recorded before
    }
    # A clean repo never gets an entry (state stays free of no-op keys).
    clean = tmp_path / "clean"
    cache_record_behind_latest(clean, "npm", 0, d0)
    assert cache_get_staleness(clean, d0) is None
    assert str(clean) not in state_mod._ensure_loaded()["behind_latest"]

    # Run 2, three days later (via disk roundtrip): totals rotate, two new
    # packages fall behind (3 -> 5).
    _new_run()
    d3 = d0 + timedelta(days=3)
    cache_record_behind_latest(repo, "npm", 5, d3)
    info = cache_get_staleness(repo, d3)
    assert info is not None
    assert info["staleness_score"] == 15.0  # 5 behind * 3 days since d0
    assert info["new_behind_since_last_run"] == 2  # max(0, 5 - 3)
    assert info["first_behind"] == "2026-07-01"
    assert info["runs"] == 2
    _reset_module_cache()


def test_zero_behind_run_resets_aging_and_prunes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _reset_module_cache()
    repo = tmp_path / "repo"
    d0 = date(2026, 7, 1)

    cache_record_behind_latest(repo, "npm", 3, d0)

    # Run 2: fully caught up — score and delta drop to zero.
    _new_run()
    cache_record_behind_latest(repo, "npm", 0, d0 + timedelta(days=2))
    info = cache_get_staleness(repo, d0 + timedelta(days=2))
    assert info is not None
    assert info["staleness_score"] == 0.0
    assert info["first_behind"] is None
    assert info["new_behind_since_last_run"] == 0

    # Run 3: behind again — the aging clock restarts at the new date rather
    # than the original d0 (reset happened when a run reported 0 behind).
    _new_run()
    d10 = d0 + timedelta(days=10)
    cache_record_behind_latest(repo, "npm", 4, d10)
    info = cache_get_staleness(repo, d10 + timedelta(days=2))
    assert info is not None
    assert info["first_behind"] == d10.isoformat()
    assert info["staleness_score"] == 8.0  # 4 behind * 2 days since d10
    assert info["new_behind_since_last_run"] == 4

    # Run 4 + 5: caught up two runs in a row — the entry is pruned entirely.
    _new_run()
    cache_record_behind_latest(repo, "npm", 0, d10 + timedelta(days=3))
    _new_run()
    cache_record_behind_latest(repo, "npm", 0, d10 + timedelta(days=4))
    assert cache_get_staleness(repo, d10 + timedelta(days=4)) is None
    assert str(repo) not in state_mod._ensure_loaded().get("behind_latest", {})
    _reset_module_cache()


def test_process_workspace_records_plan_behind_latest(tmp_path, monkeypatch, make_workspace):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("REPO_OPS_NO_PROGRESS", "1")
    _reset_module_cache()
    ws = make_workspace(tmp_path, ecosystem="node", manager="npm", label="test-repo")
    checked = WorkspaceResult(
        workspace=ws,
        status="ok",
        behind_latest=[Change(name="left", from_ver="1.0.0", to="2.0.0")],
    )
    with (
        patch("repo_ops_upgrade.orchestration.should_exclude", return_value=False),
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.check_workspace",
            return_value=checked,
        ),
        patch("repo_ops_upgrade.orchestration.remediations.run_remediations"),
    ):
        out = process_workspace(
            WorkspaceResult(workspace=ws),
            do_apply=False,
            do_verify=False,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
        )
    assert out.status == "ok"
    info = cache_get_staleness(tmp_path, date.today())
    assert info is not None
    assert info["total_behind"] == 1
    assert info["by_manager"] == {"npm": 1}
    _reset_module_cache()


def test_report_surfaces_score_delta_and_footer_top3(tmp_path, monkeypatch, make_workspace):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _reset_module_cache()
    repo = tmp_path / "repo"
    # Seed the store as if the repo was first seen behind 10 days ago, so the
    # report layer's real date.today() yields a deterministic score.
    cache_record_behind_latest(repo, "npm", 4, date.today() - timedelta(days=10))

    ws = make_workspace(repo, ecosystem="node", manager="npm", label="test-repo")
    bundle = RepoBundle(
        git_repo=repo,
        label="test-repo",
        workspaces=[
            WorkspaceResult(
                workspace=ws,
                behind_latest=[
                    Change(name=f"pkg{i}", from_ver="1.0.0", to="2.0.0") for i in range(4)
                ],
            )
        ],
    )
    payload = build_json_payload({repo: bundle}, roots=[str(repo)], mode="plan")
    repo_json = payload["repos"][0]
    assert repo_json["staleness_score"] == 40.0  # 4 behind * 10 days
    assert repo_json["new_behind_since_last_run"] == 4
    assert payload["summary"]["staleness_top"] == [{"repo": "test-repo", "score": 40.0}]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=build_run_summary({repo: bundle}), failures=[], exit_code=0)
    assert "  staleness: test-repo 40.0 (behind-count x days-behind)" in buf.getvalue()
    _reset_module_cache()


def test_empty_store_keeps_report_output_unchanged(tmp_path, monkeypatch, make_workspace):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _reset_module_cache()
    repo = tmp_path / "repo"
    bundle = RepoBundle(
        git_repo=repo,
        label="test-repo",
        workspaces=[
            WorkspaceResult(
                workspace=make_workspace(repo, ecosystem="node", manager="npm", label="test-repo"),
                behind_latest=[Change(name="pkg", from_ver="1.0.0", to="2.0.0")],
            )
        ],
    )
    summary = build_run_summary({repo: bundle})
    assert "staleness_top" not in summary
    payload = build_json_payload({repo: bundle}, roots=[str(repo)], mode="plan")
    assert "staleness_score" not in payload["repos"][0]
    assert "new_behind_since_last_run" not in payload["repos"][0]

    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    assert "staleness" not in buf.getvalue()

    # REPO_OPS_NO_STATE=1 disables both recording and read-back entirely.
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    cache_record_behind_latest(repo, "npm", 3, date.today())
    assert cache_get_staleness(repo, date.today()) is None
    _reset_module_cache()
