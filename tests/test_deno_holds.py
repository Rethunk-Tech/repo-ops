from __future__ import annotations

from unittest.mock import patch

import pytest
from repo_ops_core.holds import HoldRule
from repo_ops_js.deno import (
    _enforce_deno_version_ceilings,
    _filter_held_deno_changes,
    check_deno,
    targeted_bump_behind,
)
from repo_ops_models import Change, Workspace

_GLOBAL_HOLDS_CACHE = "repo_ops_js.deno._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_js.deno._repo_holds_cached"
_PACKAGE = "npm:held"


def _ws(tmp_path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="js",
        manager="deno",
        git_repo=tmp_path,
        label="deno",
    )


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _PACKAGE, "max_version": "1.5"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return type(
        "Proc", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr}
    )()


@pytest.fixture(autouse=True)
def _no_repo_holds():
    with patch(_REPO_HOLDS_CACHE, return_value=()):
        yield


def test_check_deno_filters_past_ceiling_from_planned_and_behind(tmp_path) -> None:
    ws = _ws(tmp_path)
    stdout = "\n".join(
        [
            "Package Current Update Latest",
            f"{_PACKAGE} 1.0.0 2.0.0 2.0.0",
            "npm:behind 1.0.0 1.0.0 2.0.0",
        ]
    )
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", return_value=_proc(stdout=stdout)),
    ):
        changes, behind = check_deno(ws)

    assert changes == []
    assert behind == [
        Change("npm:behind", "1.0.0", "2.0.0"),
    ]


def test_filter_held_deno_changes_keeps_target_within_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    change = Change(_PACKAGE, "1.0.0", "1.5.0")
    with patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)):
        assert _filter_held_deno_changes(ws, [change]) == [change]


def test_targeted_bump_skips_held_target_above_ceiling(tmp_path, monkeypatch) -> None:
    ws = _ws(tmp_path)
    change = Change(_PACKAGE, "1.0.0", "2.0.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js.deno.run") as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == []
    assert still_behind == [change]
    run_mock.assert_not_called()


def test_targeted_bump_restores_landing_above_ceiling(tmp_path, monkeypatch) -> None:
    ws = _ws(tmp_path)
    change = Change(_PACKAGE, "1.0.0", "1.4.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_js.deno.snapshot_workspace_files",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore_mock,
        patch("repo_ops_js.deno._landed_deno_version", return_value="2.0.0"),
        patch("repo_ops_js.deno.run", return_value=_proc()),
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == []
    assert still_behind == [change]
    restore_mock.assert_called_once()


def test_enforce_deno_version_ceilings_clamps_resolved_above_ceiling(tmp_path) -> None:
    ws = _ws(tmp_path)
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_js.deno._landed_deno_version",
            side_effect=["2.0.0", "1.5.0"],
        ),
        patch("repo_ops_js.deno.snapshot_workspace_files", return_value=object()),
        patch("repo_ops_js.deno.restore_workspace_snapshot") as restore_mock,
        patch("repo_ops_js.deno.run", return_value=_proc()) as run_mock,
    ):
        _enforce_deno_version_ceilings(ws, [_PACKAGE])

    assert run_mock.call_args.args[0] == ["deno", "update", _PACKAGE]
    restore_mock.assert_not_called()
