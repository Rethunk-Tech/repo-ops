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


def test_yarn_up_cmd_in_range_refresh_by_default(monkeypatch) -> None:
    """#114 — bare `yarn up` is a Yarn 4 no-op; conservative mode must pass
    `-R '*'` to actually re-resolve in-range bumps."""
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    assert _yarn_up_cmd() == ["yarn", "up", "-R", "*"]


def test_yarn_up_cmd_latest_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_YARN_UPDATE_LATEST", "1")
    assert _yarn_up_cmd() == ["yarn", "up", "-R", "*"]


def test_apply_yarn_uses_in_range_refresh_cmd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_YARN_UPDATE_LATEST", raising=False)
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="",
    )
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: object, timeout: object = None) -> MagicMock:
        calls.append(list(cmd))
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch("repo_ops_js_yarn.yarn.run", side_effect=fake_run),
        # _plan_without_resolutions runs yarn in a temp copy; short-circuit it to
        # keep this test focused on the apply command sequence.
        patch(_PLAN_PATH, return_value=[]),
    ):
        apply_yarn(ws)

    assert calls[0] == ["yarn", "up", "-R", "*"]
    assert calls[1] == ["yarn", "install"]


# ---------------------------------------------------------------------------
# PnP detection (#121)
# ---------------------------------------------------------------------------


def test_is_pnp_workspace_detects_pnp_sentinel(tmp_path: Path) -> None:
    (tmp_path / ".pnp.cjs").write_text("", encoding="utf-8")
    assert _is_pnp_workspace(tmp_path) is True


def test_is_pnp_workspace_detects_loader_sentinel(tmp_path: Path) -> None:
    (tmp_path / ".pnp.loader.mjs").write_text("", encoding="utf-8")
    assert _is_pnp_workspace(tmp_path) is True


def test_is_pnp_workspace_node_modules_linker_is_not_pnp(tmp_path: Path) -> None:
    (tmp_path / ".yarnrc.yml").write_text(
        "nodeLinker: node-modules\n", encoding="utf-8"
    )
    assert _is_pnp_workspace(tmp_path) is False


def test_is_pnp_workspace_no_yarnrc_is_not_pnp(tmp_path: Path) -> None:
    """No .yarnrc.yml implies Yarn Classic (always node-modules) — guard should run."""
    assert _is_pnp_workspace(tmp_path) is False


def test_is_pnp_workspace_missing_nodelinker_defaults_pnp(tmp_path: Path) -> None:
    """Yarn 4 default (no nodeLinker in .yarnrc.yml) is PnP — guard skipped."""
    (tmp_path / ".yarnrc.yml").write_text("enableGlobalCache: true\n", encoding="utf-8")
    assert _is_pnp_workspace(tmp_path) is True


# ---------------------------------------------------------------------------
# Ghost-bump guard wired into apply_yarn (#121)
# ---------------------------------------------------------------------------


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="test",
    )


def _node_modules_yarnrc(tmp_path: Path) -> None:
    (tmp_path / ".yarnrc.yml").write_text(
        "nodeLinker: node-modules\n", encoding="utf-8"
    )


def test_apply_yarn_guard_raises_on_blocked_target(tmp_path: Path) -> None:
    """Guard fires: probe planned is-odd→1.0.0, node_modules still at 0.1.2 → error."""
    _node_modules_yarnrc(tmp_path)
    planned = [Change("is-odd", "0.1.2", "1.0.0")]

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch(
            "repo_ops_js_yarn.yarn.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ),
        patch(_PLAN_PATH, return_value=planned),
        patch(_RESOLVER_PATH, return_value="0.1.2"),  # still behind
    ):
        try:
            apply_yarn(_ws(tmp_path))
        except RuntimeError as exc:
            assert "ghost bump" in str(exc)
            assert "is-odd" in str(exc)
        else:
            raise AssertionError("expected ghost-bump RuntimeError")


def test_apply_yarn_guard_ok_when_target_resolved(tmp_path: Path) -> None:
    """No error when node_modules has the requested version (real upgrade)."""
    _node_modules_yarnrc(tmp_path)
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
        apply_yarn(_ws(tmp_path))  # must not raise


def test_apply_yarn_guard_skipped_for_pnp_workspace(
    tmp_path: Path, capsys: object
) -> None:
    """PnP workspace: guard is skipped and a note is printed to stderr."""
    # .yarnrc.yml without nodeLinker → Yarn 4 default is PnP (no node_modules)
    (tmp_path / ".yarnrc.yml").write_text("enableGlobalCache: true\n", encoding="utf-8")
    planned = [Change("lodash", "4.17.20", "4.17.21")]
    guard_calls: list[object] = []

    def fake_guard(ws: object, requested: object, manager: str = "js") -> None:
        guard_calls.append(requested)

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch(
            "repo_ops_js_yarn.yarn.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ),
        patch(_PLAN_PATH, return_value=planned),
        patch("repo_ops_js_yarn.yarn.assert_targets_resolved", fake_guard),
    ):
        apply_yarn(_ws(tmp_path))

    assert guard_calls == [], "guard must not be called for PnP workspaces"
    captured = capsys.readouterr()
    assert "Plug'n'Play" in captured.err


def test_apply_yarn_guard_not_called_when_no_targets(tmp_path: Path) -> None:
    """No guardable changes (empty probe output) → assert_targets_resolved not called."""
    _node_modules_yarnrc(tmp_path)
    guard_calls: list[object] = []

    def fake_guard(ws: object, requested: object, manager: str = "js") -> None:
        guard_calls.append(requested)

    with (
        patch("repo_ops_js_yarn.yarn.corepack_prepare"),
        patch(
            "repo_ops_js_yarn.yarn.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ),
        patch(_PLAN_PATH, return_value=[]),
        patch("repo_ops_js_yarn.yarn.assert_targets_resolved", fake_guard),
    ):
        apply_yarn(_ws(tmp_path))

    assert guard_calls == []


# ---------------------------------------------------------------------------
# Probe cache (#127)
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


def test_probe_cache_populated_by_check_skip_in_apply(tmp_path: Path) -> None:
    """check_yarn_snapshot populates the cache; apply_yarn reuses it (probe not invoked)."""
    clear_probe_cache()
    _node_modules_yarnrc(tmp_path)
    ws = _ws(tmp_path)
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


def test_probe_cache_fallback_when_check_not_run(tmp_path: Path) -> None:
    """apply_yarn without a prior check falls back to _plan_without_resolutions."""
    clear_probe_cache()
    _node_modules_yarnrc(tmp_path)
    ws = _ws(tmp_path)
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
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Two workspaces in the same process use independent cache slots."""
    clear_probe_cache()
    ws_a_path = tmp_path
    ws_b_path = tmp_path_factory.mktemp("ws_b")
    (ws_a_path / ".yarnrc.yml").write_text(
        "nodeLinker: node-modules\n", encoding="utf-8"
    )
    (ws_b_path / ".yarnrc.yml").write_text(
        "nodeLinker: node-modules\n", encoding="utf-8"
    )
    ws_a = _ws(ws_a_path)
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


def test_probe_cache_clear_works(tmp_path: Path) -> None:
    """clear_probe_cache() empties the dict; next apply falls back to the probe."""
    clear_probe_cache()
    _node_modules_yarnrc(tmp_path)
    ws = _ws(tmp_path)
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
