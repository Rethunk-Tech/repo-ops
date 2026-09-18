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
