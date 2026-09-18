"""Yarn conservative vs latest upgrade policy."""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_js_yarn.yarn import (
    _is_pnp_workspace,
    _yarn_up_cmd,
    apply_yarn,
    check_yarn_snapshot,
    clear_probe_cache,
)
from repo_ops_models import Change, Workspace

# Canonical patch target for the ghost-bump guard resolver (same as other managers).
_RESOLVER_PATH = "repo_ops_js.guards.resolved_version_from_node_modules"
# Patch target for the pre-apply isolation probe used inside apply_yarn.
_PLAN_PATH = "repo_ops_js_yarn.yarn._plan_without_resolutions"


def test_yarn_up_cmd_latest_when_env_set(monkeypatch) -> None:
    assert _yarn_up_cmd() == ["yarn", "up", "-R", "*"]


# ---------------------------------------------------------------------------
# PnP detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        (".pnp.cjs", "", True),
        (".pnp.loader.mjs", "", True),
        (".yarnrc.yml", "nodeLinker: node-modules\n", False),
        # No .yarnrc.yml implies Yarn Classic (always node-modules) — guard should run.
        (None, None, False),
        # Yarn 4 default (no nodeLinker in .yarnrc.yml) is PnP — guard skipped.
        (".yarnrc.yml", "enableGlobalCache: true\n", True),
    ],
    ids=[
        "pnp-sentinel",
        "loader-sentinel",
        "node-modules-linker",
        "no-yarnrc",
        "missing-nodelinker-defaults-pnp",
    ],
)
def test_is_pnp_workspace(
    tmp_path: Path, filename: str | None, content: str | None, expected: bool
) -> None:
    if filename is not None:
        (tmp_path / filename).write_text(content, encoding="utf-8")
    assert _is_pnp_workspace(tmp_path) is expected


# ---------------------------------------------------------------------------
# Ghost-bump guard wired into apply_yarn
# ---------------------------------------------------------------------------


def _node_modules_yarnrc(tmp_path: Path) -> None:
    (tmp_path / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")


def _resolutions_pkg(tmp_path: Path) -> None:
    """Write a package.json with resolutions so the ghost-bump probe is eligible."""
    import json

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "resolutions": {"is-odd": "0.1.2"},
            }
        ),
        encoding="utf-8",
    )


def test_apply_yarn_guard_ok_when_target_resolved(tmp_path: Path, make_workspace) -> None:
    """No error when node_modules has the requested version (real upgrade)."""
    _node_modules_yarnrc(tmp_path)
    _resolutions_pkg(tmp_path)
    planned = [Change("is-odd", "0.1.2", "1.0.0")]

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch(
            "repo_ops_js_yarn.yarn.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ),
        patch(_PLAN_PATH, return_value=planned),
        patch(_RESOLVER_PATH, return_value="1.0.0"),  # upgraded
    ):
        apply_yarn(
            make_workspace(tmp_path, ecosystem="node", manager="yarn", label="test")
        )  # must not raise


def test_apply_yarn_guard_skipped_for_pnp_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_workspace
) -> None:
    """PnP workspace: the guard is skipped with a note on stderr."""
    # .yarnrc.yml without nodeLinker → Yarn 4 default is PnP (no node_modules)
    (tmp_path / ".yarnrc.yml").write_text("enableGlobalCache: true\n", encoding="utf-8")
    _resolutions_pkg(tmp_path)
    planned = [Change("lodash", "4.17.20", "4.17.21")]

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch(
            "repo_ops_js_yarn.yarn.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ),
        patch(_PLAN_PATH, return_value=planned),
    ):
        apply_yarn(make_workspace(tmp_path, ecosystem="node", manager="yarn", label="test"))

    captured = capsys.readouterr()
    assert "Plug'n'Play" in captured.err


# ---------------------------------------------------------------------------
# Probe cache
# ---------------------------------------------------------------------------

# Patch target for the snapshot/diff helpers inside check_yarn_snapshot.
_SNAPSHOT_PATH = "repo_ops_js_yarn.yarn.snapshot_workspace_files"
_DIFF_PATH = "repo_ops_js_yarn.yarn.diff_workspace_snapshots"
_COPY_PATH = "repo_ops_js_yarn.yarn._copy_yarn_workspace_to_temp"
_OVERRIDES_PATH = "repo_ops_js_yarn.yarn.overrides_audit"

_OK_RUN = MagicMock(returncode=0, stdout="", stderr="")


def _enter_check_patches(stack: contextlib.ExitStack, ws_path: Path) -> None:
    """Enter stubs for check_yarn_snapshot into *stack*."""
    stack.enter_context(patch("repo_ops_js_yarn.yarn.corepack_prepare"))
    stack.enter_context(patch("repo_ops_js_yarn.yarn.run", return_value=_OK_RUN))
    stack.enter_context(patch(_COPY_PATH, return_value=ws_path))
    stack.enter_context(patch(_SNAPSHOT_PATH, return_value={}))
    stack.enter_context(patch(_DIFF_PATH, return_value=[]))
    stack.enter_context(patch(_OVERRIDES_PATH, return_value=[]))


def test_probe_cache_populated_by_check_skip_in_apply(tmp_path: Path, make_workspace) -> None:
    """check_yarn_snapshot populates the cache; apply_yarn reuses it (probe not invoked)."""
    clear_probe_cache()
    _node_modules_yarnrc(tmp_path)
    _resolutions_pkg(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="yarn", label="test")
    planned = [Change("lodash", "4.17.20", "4.17.21")]

    probe_calls: list[Path] = []

    def fake_probe(w: Workspace) -> list[Change]:
        probe_calls.append(w.path)
        return planned

    # Run check — it calls _plan_without_resolutions via fake_probe and caches.
    with contextlib.ExitStack() as stack:
        _enter_check_patches(stack, tmp_path)
        stack.enter_context(patch(_PLAN_PATH, side_effect=fake_probe))
        check_yarn_snapshot(ws)

    assert len(probe_calls) == 1, "check should invoke the probe exactly once"

    # Now apply — probe must NOT be called again (cache hit).
    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", return_value=_OK_RUN),
        patch(_PLAN_PATH, side_effect=fake_probe),
        patch(_RESOLVER_PATH, return_value="4.17.21"),  # guard sees upgraded version
    ):
        apply_yarn(ws)

    assert len(probe_calls) == 1, "apply should reuse cache — probe must not run again"


def test_probe_cache_fallback_when_check_not_run(tmp_path: Path, make_workspace) -> None:
    """apply_yarn without a prior check falls back to _plan_without_resolutions."""
    clear_probe_cache()
    _node_modules_yarnrc(tmp_path)
    _resolutions_pkg(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="yarn", label="test")
    planned = [Change("is-odd", "0.1.2", "1.0.0")]

    probe_calls: list[Path] = []

    def fake_probe(w: Workspace) -> list[Change]:
        probe_calls.append(w.path)
        return planned

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", return_value=_OK_RUN),
        patch(_PLAN_PATH, side_effect=fake_probe),
        patch(_RESOLVER_PATH, return_value="1.0.0"),  # guard satisfied
    ):
        apply_yarn(ws)

    assert len(probe_calls) == 1, "apply without prior check must invoke the probe"


def test_probe_cache_keyed_per_workspace(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, make_workspace
) -> None:
    """Two workspaces in the same process use independent cache slots."""
    clear_probe_cache()
    ws_a_path = tmp_path
    ws_b_path = tmp_path_factory.mktemp("ws_b")
    (ws_a_path / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")
    (ws_b_path / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")
    _resolutions_pkg(ws_a_path)
    _resolutions_pkg(ws_b_path)
    ws_a = make_workspace(ws_a_path, ecosystem="node", manager="yarn", label="test")
    ws_b = Workspace(
        path=ws_b_path,
        ecosystem="node",
        manager="yarn",
        git_repo=ws_b_path,
        label="ws-b",
    )

    planned_a = [Change("react", "18.0.0", "18.3.1")]
    planned_b = [Change("vue", "3.0.0", "3.4.0")]

    def fake_probe(w: Workspace) -> list[Change]:
        if w.path.resolve() == ws_a_path.resolve():
            return planned_a
        return planned_b

    # Populate cache for both workspaces via check.
    for ws, ws_path in [(ws_a, ws_a_path), (ws_b, ws_b_path)]:
        with contextlib.ExitStack() as stack:
            _enter_check_patches(stack, ws_path)
            stack.enter_context(patch(_PLAN_PATH, side_effect=fake_probe))
            check_yarn_snapshot(ws)

    probe_calls: list[Path] = []

    def counting_probe(w: Workspace) -> list[Change]:
        probe_calls.append(w.path)
        return []

    # Apply on ws_a — must use ws_a's cached targets (react), not ws_b's.
    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", return_value=_OK_RUN),
        patch(_PLAN_PATH, side_effect=counting_probe),
        patch(_RESOLVER_PATH, return_value="18.3.1"),
    ):
        apply_yarn(ws_a)

    assert probe_calls == [], "ws_a apply must hit the cache, not call probe"

    # Apply on ws_b — must use ws_b's cached targets (vue), not ws_a's.
    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", return_value=_OK_RUN),
        patch(_PLAN_PATH, side_effect=counting_probe),
        patch(_RESOLVER_PATH, return_value="3.4.0"),
    ):
        apply_yarn(ws_b)

    assert probe_calls == [], "ws_b apply must hit its own cache slot, not call probe"


def test_probe_cache_clear_works(tmp_path: Path, make_workspace) -> None:
    """clear_probe_cache() empties the dict; next apply falls back to the probe."""
    clear_probe_cache()
    _node_modules_yarnrc(tmp_path)
    _resolutions_pkg(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="node", manager="yarn", label="test")
    planned = [Change("lodash", "4.17.20", "4.17.21")]

    # Populate the cache via check.
    with contextlib.ExitStack() as stack:
        _enter_check_patches(stack, tmp_path)
        stack.enter_context(patch(_PLAN_PATH, return_value=planned))
        check_yarn_snapshot(ws)

    # Clear the cache.
    clear_probe_cache()

    probe_calls: list[Path] = []

    def counting_probe(w: Workspace) -> list[Change]:
        probe_calls.append(w.path)
        return []

    # Apply after clear — must call the probe again (cache miss).
    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", return_value=_OK_RUN),
        patch(_PLAN_PATH, side_effect=counting_probe),
    ):
        apply_yarn(ws)

    assert len(probe_calls) == 1, "after clear(), apply must fall back to the probe"
