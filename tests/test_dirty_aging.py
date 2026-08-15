"""Dirty-skip aging: persistent first-seen tracking (#dirty-aging)."""

from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core import state as state_mod
from repo_ops_core.state import (
    clear_dirty_skip,
    load_state,
    record_dirty_skip,
    save_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_module_cache() -> None:
    """Reset the module-level lazy cache between tests."""
    state_mod._CACHE = None
    state_mod._DIRTY = False


# ---------------------------------------------------------------------------
# state.py — explicit dict API
# ---------------------------------------------------------------------------


def test_record_dirty_skip_first_call_sets_first_seen() -> None:
    state: dict = {}
    today = date(2026, 6, 11)
    first_seen = record_dirty_skip(state, "/repo/alpha", today)
    assert first_seen == today
    entry = state["dirty_skips"]["/repo/alpha"]
    assert entry["first_seen"] == "2026-06-11"
    assert entry["runs"] == 1


def test_record_dirty_skip_repeat_increments_runs() -> None:
    state: dict = {}
    d0 = date(2026, 6, 1)
    d1 = date(2026, 6, 5)
    record_dirty_skip(state, "/repo/alpha", d0)
    second = record_dirty_skip(state, "/repo/alpha", d1)
    # first_seen must not change on subsequent calls
    assert second == d0
    assert state["dirty_skips"]["/repo/alpha"]["runs"] == 2


def test_clear_dirty_skip_removes_entry() -> None:
    state: dict = {}
    record_dirty_skip(state, "/repo/alpha", date(2026, 6, 1))
    clear_dirty_skip(state, "/repo/alpha")
    assert "/repo/alpha" not in state.get("dirty_skips", {})


def test_clear_dirty_skip_noop_when_absent() -> None:
    state: dict = {}
    # Must not raise
    clear_dirty_skip(state, "/repo/alpha")


def test_record_dirty_skip_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    state: dict = {}
    result = record_dirty_skip(state, "/repo/alpha", date(2026, 6, 1))
    assert result is None
    assert state == {}


def test_clear_dirty_skip_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    # Pre-populate without the env var and then clear with it — must be no-op
    state = {"dirty_skips": {"/repo/alpha": {"first_seen": "2026-06-01", "runs": 1}}}
    clear_dirty_skip(state, "/repo/alpha")
    # Entry should remain because REPO_OPS_NO_STATE suppresses the op
    assert "/repo/alpha" in state["dirty_skips"]


# ---------------------------------------------------------------------------
# state.py — load/save roundtrip
# ---------------------------------------------------------------------------


def test_load_save_roundtrip(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}):
        state = load_state()
        record_dirty_skip(state, "/repo/alpha", date(2026, 6, 1))
        save_state(state)

        loaded = load_state()
    assert loaded["dirty_skips"]["/repo/alpha"]["first_seen"] == "2026-06-01"
    assert loaded["dirty_skips"]["/repo/alpha"]["runs"] == 1


def test_load_state_tolerates_corrupt_file(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache" / "repo-ops"
    cache_dir.mkdir(parents=True)
    (cache_dir / "state.json").write_text("NOT JSON {{{", encoding="utf-8")
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}):
        state = load_state()
    assert state == {}


def test_load_state_tolerates_missing_file(tmp_path: Path) -> None:
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "no-cache")}):
        state = load_state()
    assert state == {}


def test_load_state_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    state = load_state()
    assert state == {}


def test_save_state_disabled_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}):
        save_state({"dirty_skips": {"/x": {"first_seen": "2026-06-01", "runs": 1}}})
        # File must not have been written
        state_file = tmp_path / "cache" / "repo-ops" / "state.json"
        assert not state_file.exists()


# ---------------------------------------------------------------------------
# state.py — module-level cache helpers
# ---------------------------------------------------------------------------


def test_cache_record_and_get(tmp_path: Path) -> None:
    _reset_module_cache()
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}):
        first_seen = state_mod.cache_record_dirty_skip("/repo/beta", date(2026, 6, 10))
    assert first_seen == date(2026, 6, 10)
    assert state_mod._DIRTY is True
    entry = state_mod.cache_get_dirty_skip("/repo/beta")
    assert entry is not None
    assert entry["runs"] == 1
    _reset_module_cache()


def test_cache_clear_removes_entry(tmp_path: Path) -> None:
    _reset_module_cache()
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}):
        state_mod.cache_record_dirty_skip("/repo/gamma", date(2026, 6, 1))
        state_mod.cache_clear_dirty_skip("/repo/gamma")
    assert state_mod.cache_get_dirty_skip("/repo/gamma") is None
    _reset_module_cache()


def test_cache_disabled_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_module_cache()
    monkeypatch.setenv("REPO_OPS_NO_STATE", "1")
    result = state_mod.cache_record_dirty_skip("/repo/delta", date(2026, 6, 1))
    assert result is None
    assert state_mod._DIRTY is False
    _reset_module_cache()


# ---------------------------------------------------------------------------
# Orchestration wiring
# ---------------------------------------------------------------------------


def _make_ws(tmp_path: Path, *, manager: str = "npm"):
    from repo_ops_models import Workspace

    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager=manager,
        git_repo=tmp_path,
        label="test-repo",
    )


def test_dirty_skip_records_in_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dirty-skip in process_workspace records an entry in the module cache."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from repo_ops_models import WorkspaceResult
    from repo_ops_upgrade.orchestration import process_workspace

    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    with (
        patch("repo_ops_upgrade.orchestration.should_exclude", return_value=False),
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=True),
        patch.dict(os.environ, {"REPO_OPS_NO_PROGRESS": "1"}),
    ):
        out = process_workspace(
            result,
            do_apply=True,
            do_verify=False,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
        )

    assert out.status == "skip"
    assert out.error is not None and "dirty git worktree" in out.error
    entry = state_mod.cache_get_dirty_skip(tmp_path)
    assert entry is not None
    assert entry["runs"] == 1
    _reset_module_cache()


def test_repo_started_dirty_override_skips_even_when_live_check_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit repo_started_dirty=True is authoritative over a live-clean
    is_git_dirty check — this is what lets the caller snapshot dirty state once
    per bundle. Issue #152."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from repo_ops_models import WorkspaceResult
    from repo_ops_upgrade.orchestration import process_workspace

    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    with (
        patch("repo_ops_upgrade.orchestration.should_exclude", return_value=False),
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=False),
        patch.dict(os.environ, {"REPO_OPS_NO_PROGRESS": "1"}),
    ):
        out = process_workspace(
            result,
            do_apply=True,
            do_verify=False,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
            repo_started_dirty=True,
        )

    assert out.status == "skip"
    assert out.error is not None and "dirty git worktree" in out.error
    _reset_module_cache()


def test_repo_started_dirty_override_proceeds_even_when_live_check_is_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit repo_started_dirty=False is authoritative over a live-dirty
    is_git_dirty check — this is the actual bug fix: a later sibling workspace
    must not be skipped just because an earlier sibling in the same bundle
    already applied its own (not yet committed) changes. Issue #152."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from repo_ops_models import WorkspaceResult
    from repo_ops_upgrade.orchestration import process_workspace

    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    with (
        patch("repo_ops_upgrade.orchestration.should_exclude", return_value=False),
        # Live check says dirty (simulating an earlier sibling's uncommitted
        # apply) — the explicit snapshot value must win instead.
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=True),
        patch(
            "repo_ops_upgrade.orchestration.snapshot_workspace_files", return_value={}
        ),
        patch(
            "repo_ops_upgrade.orchestration.diff_workspace_snapshots", return_value=[]
        ),
        patch(
            "repo_ops_upgrade.orchestration.collect_changes_after_apply",
            return_value=[],
        ),
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace", return_value=[]
        ),
        patch("repo_ops_upgrade.orchestration.remediations.run_remediations"),
        patch.dict(os.environ, {"REPO_OPS_NO_PROGRESS": "1"}),
    ):
        out = process_workspace(
            result,
            do_apply=True,
            do_verify=False,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
            repo_started_dirty=False,
        )

    assert out.status == "ok"
    assert out.started_dirty is False
    _reset_module_cache()


def test_clean_run_clears_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean (non-dirty) apply run removes any existing dirty-skip entry."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Pre-populate cache entry
    state_mod.cache_record_dirty_skip(str(tmp_path), date(2026, 6, 1))
    assert state_mod.cache_get_dirty_skip(str(tmp_path)) is not None

    from repo_ops_models import WorkspaceResult
    from repo_ops_upgrade.orchestration import process_workspace

    ws = _make_ws(tmp_path)
    result = WorkspaceResult(workspace=ws)

    with (
        patch("repo_ops_upgrade.orchestration.should_exclude", return_value=False),
        patch("repo_ops_upgrade.orchestration.is_git_dirty", return_value=False),
        patch(
            "repo_ops_upgrade.orchestration.snapshot_workspace_files", return_value={}
        ),
        patch(
            "repo_ops_upgrade.orchestration.diff_workspace_snapshots", return_value=[]
        ),
        patch(
            "repo_ops_upgrade.orchestration.collect_changes_after_apply",
            return_value=[],
        ),
        patch(
            "repo_ops_upgrade.orchestration.ecosystems.apply_workspace", return_value=[]
        ),
        patch("repo_ops_upgrade.orchestration.remediations.run_remediations"),
        patch.dict(os.environ, {"REPO_OPS_NO_PROGRESS": "1"}),
    ):
        out = process_workspace(
            result,
            do_apply=True,
            do_verify=False,
            verify_test=False,
            skip_remediations=set(),
            allow_dirty=False,
        )

    assert out.status == "ok"
    assert state_mod.cache_get_dirty_skip(str(tmp_path)) is None
    _reset_module_cache()


# ---------------------------------------------------------------------------
# Report enrichment — human output
# ---------------------------------------------------------------------------


def test_human_report_skip_shows_aging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Human report skip line includes dirty-since + runs when state has an entry."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Seed the module cache
    state_mod.cache_record_dirty_skip(str(tmp_path), date(2026, 5, 1))
    state_mod.cache_record_dirty_skip(str(tmp_path), date(2026, 5, 2))

    from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import print_human_report

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="test-repo",
    )
    bundle = RepoBundle(
        git_repo=tmp_path,
        label="test-repo",
        workspaces=[
            WorkspaceResult(
                workspace=ws,
                status="skip",
                error="dirty git worktree (use --allow-dirty)",
            )
        ],
    )
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=False,
            only_outdated=False,
            top_n=10,
            include_actions=False,
        )

    out = buf.getvalue()
    assert "dirty since 2026-05-01" in out
    assert "2 run(s)" in out
    _reset_module_cache()


def test_human_report_skip_no_aging_when_no_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Human report skip line shows plain error when no state entry exists."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # No cache entry for this path

    from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import print_human_report

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="test-repo",
    )
    bundle = RepoBundle(
        git_repo=tmp_path,
        label="test-repo",
        workspaces=[
            WorkspaceResult(
                workspace=ws,
                status="skip",
                error="dirty git worktree (use --allow-dirty)",
            )
        ],
    )
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=False,
            only_outdated=False,
            top_n=10,
            include_actions=False,
        )

    out = buf.getvalue()
    # Plain error, no aging annotation
    assert "dirty git worktree" in out
    assert "dirty since" not in out
    _reset_module_cache()


# ---------------------------------------------------------------------------
# Report enrichment — JSON payload
# ---------------------------------------------------------------------------


def test_json_payload_dirty_skip_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON workspace entry gains dirty_since + dirty_skip_runs on dirty skip."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    state_mod.cache_record_dirty_skip(str(tmp_path), date(2026, 6, 1))

    from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import build_json_payload

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="test-repo",
    )
    bundle = RepoBundle(
        git_repo=tmp_path,
        label="test-repo",
        workspaces=[
            WorkspaceResult(
                workspace=ws,
                status="skip",
                error="dirty git worktree (use --allow-dirty)",
            )
        ],
    )
    payload = build_json_payload(
        {tmp_path: bundle}, roots=[str(tmp_path)], mode="apply"
    )
    ws_entry = payload["repos"][0]["workspaces"][0]
    assert ws_entry.get("dirty_since") == "2026-06-01"
    assert ws_entry.get("dirty_skip_runs") == 1
    _reset_module_cache()


def test_json_payload_no_dirty_fields_for_clean_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean workspace entries must not have dirty_since / dirty_skip_runs."""
    _reset_module_cache()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
    from repo_ops_upgrade.report import build_json_payload

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="test-repo",
    )
    bundle = RepoBundle(
        git_repo=tmp_path,
        label="test-repo",
        workspaces=[WorkspaceResult(workspace=ws, status="ok")],
    )
    payload = build_json_payload({tmp_path: bundle}, roots=[str(tmp_path)], mode="plan")
    ws_entry = payload["repos"][0]["workspaces"][0]
    assert "dirty_since" not in ws_entry
    assert "dirty_skip_runs" not in ws_entry
    _reset_module_cache()
