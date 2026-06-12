"""Yarn conservative vs latest upgrade policy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_js_yarn.yarn import _is_pnp_workspace, _yarn_up_cmd, apply_yarn
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
