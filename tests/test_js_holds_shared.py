"""Direct unit coverage for repo_ops_js.holds — the shared enforcement layer
reused by npm/yarn/pnpm/bun. Tests here exercise branches once instead of
duplicating them per ecosystem (see test_js_holds_wiring.py for the
per-manager wiring).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.holds import HoldRule
from repo_ops_core.holds import workspace_rel as _workspace_rel
from repo_ops_js.holds import ceiling_version as _REAL_CEILING_VERSION
from repo_ops_js.holds import enforce_holds
from repo_ops_models import Workspace

_GLOBAL_HOLDS_CACHE = "repo_ops_core.holds._global_holds_cached"


def test_workspace_rel_falls_back_when_path_outside_git_repo(tmp_path: Path) -> None:
    """A workspace path that isn't under git_repo (e.g. discovery edge case)
    must not raise — it degrades to repo-wide matching (".") rather than
    crashing hold resolution."""
    other_root = tmp_path / "unrelated"
    other_root.mkdir()
    ws = Workspace(
        path=other_root,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path / "actual-repo",
        label="demo",
    )
    assert _workspace_rel(ws) == "."


def test_enforce_holds_raises_when_corrective_install_command_fails(
    tmp_path: Path,
) -> None:
    """Distinct failure mode from 'installed but still over ceiling': the
    corrective install command itself errors out (e.g. registry down,
    invalid spec) — this must surface as a loud failure too."""
    nm_dir = tmp_path / "node_modules" / "typescript"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text('{"version": "7.2.0"}', encoding="utf-8")
    ws = Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label="demo")
    rule = HoldRule(package="typescript", max_version="6")

    def _failing_install(name: str, spec: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["npm", "install", f"{name}@{spec}"], 1, stdout="", stderr="network error"
        )

    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(rule,)),
        pytest.raises(RuntimeError, match=r"hold.*typescript.*network error"),
    ):
        enforce_holds(ws, ["typescript"], manager="npm", run_install=_failing_install)


def _npm_view(versions: list[str]):
    import json

    def fake_run(cmd, cwd, timeout=None, env=None):
        assert cmd[:2] == ["npm", "view"]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(versions), stderr="")

    return fake_run


def test_ceiling_version_is_the_highest_release_the_registry_lists() -> None:
    from repo_ops_js import holds as js_holds

    ws = Workspace(path=Path("."), ecosystem="node", manager="npm", git_repo=Path("."), label="d")
    rule = HoldRule(package="typescript", max_version="5")
    listed = ["5.0.4", "5.9.3", "5.10.0-rc.1", "5.2.2"]
    with (
        patch.object(js_holds, "ceiling_version", _REAL_CEILING_VERSION),
        patch.object(js_holds, "run", _npm_view(listed)),
    ):
        assert js_holds.ceiling_version(ws, "typescript", rule) == "5.9.3"
        assert js_holds.hold_install_spec(ws, "typescript", rule) == "^5.9.3"


def test_plan_shows_the_version_the_ceiling_allows_for_a_held_package(tmp_path: Path) -> None:
    from repo_ops_js import holds as js_holds
    from repo_ops_models import Change

    ws = Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label="d")
    rule = HoldRule(package="typescript", max_version="5.4")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(rule,)),
        patch.object(js_holds, "ceiling_version", return_value="5.4.5"),
    ):
        in_range = js_holds.plan_held_changes(ws, [Change("typescript", "5.0.4", "5.9.3")], [])
        held = js_holds.plan_held_changes(ws, [], [Change("typescript", "4.9.5", "7.0.2")])
    assert in_range[0] == [Change("typescript", "5.0.4", "5.4.5")]
    assert held == (
        [Change("typescript", "4.9.5", "5.4.5")],
        [Change("typescript", "4.9.5", "7.0.2")],
    )


@pytest.mark.parametrize(
    ("ceiling", "spec", "exact"),
    [("5", "^5.4.5", False), ("5.4", "~5.4.5", False), ("5.4.5", "5.4.5", True)],
)
def test_hold_install_spec_saves_a_range_matching_the_ceiling(
    ceiling: str, spec: str, exact: bool
) -> None:
    from repo_ops_js import holds as js_holds

    ws = Workspace(path=Path("."), ecosystem="node", manager="npm", git_repo=Path("."), label="d")
    rule = HoldRule(package="typescript", max_version=ceiling)
    with patch.object(js_holds, "ceiling_version", return_value="5.4.5"):
        assert js_holds.hold_install_spec(ws, "typescript", rule) == spec
    assert js_holds.exact_install_flag(spec, "--save-exact") == (["--save-exact"] if exact else [])
