"""A declared [generate] command runs after a bump and its output is committed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from repo_ops_models import RepoBundle, WorkspaceResult
from repo_ops_upgrade import orchestration
from repo_ops_upgrade.post_apply_commit import plan_repo_commit


def test_generate_runs_after_bump_and_output_lands_in_commit(
    tmp_path: Path, monkeypatch, init_git_repo, make_workspace
) -> None:
    init_git_repo(tmp_path, commit=True, filename="package.json", content="{}\n")
    (tmp_path / "gen.py").write_text(
        "from pathlib import Path\nPath('CREDITS').write_text('ok\\n')\n"
    )
    (tmp_path / ".repo-ops.toml").write_text(
        "[generate]\nrun = " + json.dumps(f"{sys.executable} gen.py") + "\n"
    )
    subprocess.run(["git", "add", "gen.py", ".repo-ops.toml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "generate"], cwd=tmp_path, check=True)

    def _apply(workspace) -> list:
        (workspace.path / "package.json").write_text('{"name":"changed"}\n')
        return []

    monkeypatch.setattr(orchestration.ecosystems, "apply_workspace", _apply)
    monkeypatch.setattr(orchestration, "dispatch_remediations", lambda *_a, **_k: None)
    monkeypatch.setattr(orchestration.verify_mod, "verify_workspace", lambda *_a, **_k: None)

    result = orchestration._process_workspace_body(
        WorkspaceResult(workspace=make_workspace(tmp_path)),
        do_apply=True,
        do_verify=True,
        allow_dirty=False,
        on_verify_fail="report",
    )
    assert (tmp_path / "CREDITS").read_text() == "ok\n"
    plan = plan_repo_commit(RepoBundle(git_repo=tmp_path, label="demo", workspaces=[result]))
    assert "CREDITS" in plan.files
