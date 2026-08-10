"""Tests for Deno's targeted behind-latest fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js.deno import (
    _landed_deno_version,
    apply_deno,
    targeted_bump_behind,
)
from repo_ops_models import Change, Workspace


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="deno",
        manager="deno",
        git_repo=tmp_path,
        label="demo",
    )


def test_targeted_bump_is_env_gated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_DENO_TARGETED_BUMP", raising=False)
    behind = [Change("npm:lodash", "4.17.20", "4.17.21")]

    with patch("repo_ops_js.deno.run") as run_mock:
        assert targeted_bump_behind(_ws(tmp_path), behind) == ([], behind)

    run_mock.assert_not_called()


def test_landed_deno_version_picks_highest_lock_version(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "repo_ops_js.deno.versions_deno_lock",
        lambda _: {
            "npm:lodash@4.17.20",
            "npm:lodash@4.9.0",
            "npm:lodash@4.17.21",
        },
    )

    assert _landed_deno_version(_ws(tmp_path), "npm:lodash") == "4.17.21"


def test_targeted_bump_keeps_changed_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("npm:lodash", "4.17.20", "4.17.21")
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[{"deno.lock": b"old"}, {"deno.lock": b"new"}],
        ),
        patch(
            "repo_ops_js.deno.versions_deno_lock",
            return_value={"npm:lodash@4.17.22"},
        ),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore,
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        applied, still_behind = targeted_bump_behind(_ws(tmp_path), [change])

    assert applied == [Change("npm:lodash", "4.17.20", "4.17.22")]
    assert still_behind == []
    assert captured == [["deno", "update", "npm:lodash"]]
    restore.assert_not_called()


def test_targeted_bump_restores_snapshot_when_guard_raises(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("npm:lodash", "4.17.20", "4.17.21")
    snapshot = {"deno.lock": b"original"}
    ws = _ws(tmp_path)

    with (
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[snapshot, {"deno.lock": b"changed"}],
        ),
        patch(
            "repo_ops_js.deno.versions_deno_lock",
            return_value={"npm:lodash@4.17.22"},
        ),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore,
        patch(
            "repo_ops_js.deno.assert_targets_resolved",
            side_effect=RuntimeError("ghost bump"),
        ) as guard,
        patch(
            "repo_ops_js.deno.run",
            return_value=subprocess.CompletedProcess(
                ["deno", "update", "npm:lodash"],
                0,
                stdout="",
                stderr="",
            ),
        ),
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == []
    assert still_behind == [change]
    guard.assert_called_once_with(
        ws,
        [Change("npm:lodash", "4.17.20", "4.17.22")],
        manager="deno",
    )
    restore.assert_called_once_with(tmp_path, snapshot, "deno")


def test_targeted_bump_restores_after_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("jsr:@std/assert", "0.221.0", "0.226.0")
    snapshot = {"deno.lock": b"original"}

    with (
        patch("repo_ops_js.deno.snapshot_workspace_files", return_value=snapshot),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore,
        patch(
            "repo_ops_js.deno.run",
            return_value=subprocess.CompletedProcess(
                ["deno", "update", "jsr:@std/assert"],
                1,
                stdout="",
                stderr="update failed",
            ),
        ),
    ):
        applied, still_behind = targeted_bump_behind(_ws(tmp_path), [change])

    assert applied == []
    assert still_behind == [change]
    restore.assert_called_once_with(tmp_path, snapshot, "deno")


def test_targeted_bump_restores_when_snapshot_does_not_advance(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("npm:chalk", "5.2.0", "5.3.0")
    snapshot = {"deno.lock": b"original"}

    with (
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[snapshot, snapshot],
        ),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore,
        patch(
            "repo_ops_js.deno.run",
            return_value=subprocess.CompletedProcess(
                ["deno", "update", "npm:chalk"],
                0,
                stdout="",
                stderr="",
            ),
        ),
    ):
        applied, still_behind = targeted_bump_behind(_ws(tmp_path), [change])

    assert applied == []
    assert still_behind == [change]
    restore.assert_called_once_with(tmp_path, snapshot, "deno")


def test_targeted_bump_restores_when_landed_version_does_not_advance(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("npm:chalk", "5.2.0", "5.3.0")
    snapshot = {"deno.lock": b"original"}

    with (
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[snapshot, {"deno.lock": b"changed"}],
        ),
        patch(
            "repo_ops_js.deno.versions_deno_lock",
            return_value={"npm:chalk@5.2.0"},
        ),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore,
        patch(
            "repo_ops_js.deno.run",
            return_value=subprocess.CompletedProcess(
                ["deno", "update", "npm:chalk"],
                0,
                stdout="",
                stderr="",
            ),
        ),
    ):
        applied, still_behind = targeted_bump_behind(_ws(tmp_path), [change])

    assert applied == []
    assert still_behind == [change]
    restore.assert_called_once_with(tmp_path, snapshot, "deno")


def test_targeted_bump_passes_latest_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "true")
    change = Change("npm:chalk", "5.2.0", "5.3.0")
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[{"deno.lock": b"old"}, {"deno.lock": b"new"}],
        ),
        patch(
            "repo_ops_js.deno.versions_deno_lock",
            return_value={"npm:chalk@5.3.0"},
        ),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
    ):
        applied, still_behind = targeted_bump_behind(_ws(tmp_path), [change])

    assert applied == [change]
    assert still_behind == []
    assert captured == [["deno", "update", "--latest", "npm:chalk"]]


def test_targeted_bump_restores_when_landed_version_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("npm:chalk", "5.2.0", "5.3.0")
    snapshot = {"deno.lock": b"original"}

    with (
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[snapshot, {"deno.lock": b"changed"}],
        ),
        patch("repo_ops_js.deno.versions_deno_lock", return_value=set()),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore,
        patch(
            "repo_ops_js.deno.run",
            return_value=subprocess.CompletedProcess(
                ["deno", "update", "npm:chalk"],
                0,
                stdout="",
                stderr="",
            ),
        ),
    ):
        applied, still_behind = targeted_bump_behind(_ws(tmp_path), [change])

    assert applied == []
    assert still_behind == [change]
    restore.assert_called_once_with(tmp_path, snapshot, "deno")


def test_apply_deno_runs_targeted_fallback_after_bulk(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    change = Change("npm:lodash", "4.17.20", "4.17.21")
    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.check_deno", return_value=([], [change])),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
        patch(
            "repo_ops_js.deno.targeted_bump_behind",
            return_value=([], [change]),
        ) as targeted,
    ):
        result = apply_deno(_ws(tmp_path))

    assert captured == [["deno", "outdated", "--update"]]
    targeted.assert_called_once_with(_ws(tmp_path), [change])
    assert result == [change]
