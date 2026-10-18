"""A fleet-sweep verify failure returns the repo to its exact pre-apply state.

Covers what a manager-lockfile snapshot alone misses: files a remediation hook
rewrote (biome.json ``$schema``), a verify that raises instead of returning an
error, an operator's pre-existing edit to a file apply also rewrote, the
installed dependency tree, and sibling workspaces whose repo commit is vetoed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops._register import register_all
from repo_ops_core import process
from repo_ops_models import Workspace, WorkspaceResult
from repo_ops_upgrade import orchestration
from repo_ops_upgrade.post_apply import FAILED_CLEAN
from repo_ops_upgrade.run import main_apply


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _commit_tree(repo: Path, files: dict[str, str], init_git_repo) -> None:
    init_git_repo(repo)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")


def _process(ws: Workspace, *, allow_dirty: bool = False) -> WorkspaceResult:
    return orchestration.process_workspace(
        WorkspaceResult(workspace=ws),
        do_apply=True,
        do_verify=True,
        allow_dirty=allow_dirty,
        on_verify_fail="revert",
    )


def test_revert_restores_remediation_edits_and_reinstalls(tmp_path: Path, init_git_repo) -> None:
    ws_dir = tmp_path / "e2e"
    _commit_tree(
        tmp_path,
        {
            "e2e/package.json": '{"devDependencies":{"@biomejs/biome":"2.5.13"}}\n',
            "e2e/bun.lock": "lock 2.5.13\n",
            "e2e/biome.json": '{"$schema":"https://biomejs.dev/schemas/2.5.13/schema.json"}\n',
        },
        init_git_repo,
    )
    (ws_dir / "node_modules").mkdir()
    ws = Workspace(path=ws_dir, ecosystem="node", manager="bun", git_repo=tmp_path, label="e2e")

    def _apply(_ws: Workspace) -> None:
        (ws_dir / "package.json").write_text('{"devDependencies":{"@biomejs/biome":"2.5.14"}}\n')
        (ws_dir / "bun.lock").write_text("lock 2.5.14\n")

    def _biome_migrate(result: WorkspaceResult, **_kw) -> None:
        (ws_dir / "biome.json").write_text(
            '{"$schema":"https://biomejs.dev/schemas/2.5.14/schema.json"}\n'
        )
        result.remediations.append(
            {"id": "biome_schema", "status": "applied", "files": ["biome.json"]}
        )

    installs: list[tuple[list[str], Path]] = []
    real_run = process.run

    def _spy_run(cmd: list[str], cwd, **kw):
        if cmd[0] == "bun":
            installs.append((cmd, Path(cwd)))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, cwd, **kw)

    register_all()
    with (
        patch("repo_ops_upgrade.orchestration.ecosystems.apply_workspace", side_effect=_apply),
        patch(
            "repo_ops_upgrade.orchestration.dispatch_remediations",
            side_effect=_biome_migrate,
        ),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            return_value="lint failed",
        ),
        patch("repo_ops_upgrade.post_apply.run", side_effect=_spy_run),
    ):
        out = _process(ws)

    assert out.status == "fail"
    assert _git(tmp_path, "status", "--porcelain") == ""
    assert out.reverted_ok is True
    assert out.verify_fail_disposition == FAILED_CLEAN
    assert installs == [(["bun", "install", "--frozen-lockfile"], ws_dir)]


def test_revert_runs_when_verify_raises(tmp_path: Path, init_git_repo) -> None:
    _commit_tree(tmp_path, {"go.mod": "module x\n", "go.sum": ""}, init_git_repo)
    ws = Workspace(path=tmp_path, ecosystem="go", manager="go", git_repo=tmp_path, label=".")

    def _apply(_ws: Workspace) -> None:
        (tmp_path / "go.mod").write_text("module x\n\nrequire y v1.0.1\n")
        (tmp_path / "go.sum").write_text("y v1.0.1 h1:abc\n")

    register_all()
    with (
        patch("repo_ops_upgrade.orchestration.ecosystems.apply_workspace", side_effect=_apply),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations"),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            side_effect=subprocess.TimeoutExpired(cmd=["gate"], timeout=60),
        ),
    ):
        out = _process(ws)

    assert out.status == "fail"
    assert "timed out" in (out.verify_error or "")
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_revert_keeps_preexisting_edit_to_a_file_apply_rewrote(
    tmp_path: Path, init_git_repo
) -> None:
    _commit_tree(
        tmp_path,
        {"package.json": '{"name":"a"}\n', "package-lock.json": "{}\n", "notes.txt": "v1\n"},
        init_git_repo,
    )
    ws = Workspace(path=tmp_path, ecosystem="node", manager="npm", git_repo=tmp_path, label=".")
    operator_manifest = '{"name":"a","scripts":{"x":"y"}}\n'
    (tmp_path / "package.json").write_text(operator_manifest)
    (tmp_path / "notes.txt").write_text("v2\n")
    (tmp_path / "scratch.md").write_text("mine\n")

    def _apply(_ws: Workspace) -> None:
        (tmp_path / "package.json").write_text('{"name":"a","dependencies":{"b":"2"}}\n')
        (tmp_path / "package-lock.json").write_text('{"b":"2"}\n')
        (tmp_path / "yarn.lock").write_text("created\n")

    register_all()
    with (
        patch("repo_ops_upgrade.orchestration.ecosystems.apply_workspace", side_effect=_apply),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations"),
        patch(
            "repo_ops_upgrade.orchestration.verify_mod.verify_workspace",
            return_value="fail",
        ),
    ):
        _process(ws, allow_dirty=True)

    assert (tmp_path / "package.json").read_text() == operator_manifest
    assert (tmp_path / "package-lock.json").read_text() == "{}\n"
    assert (tmp_path / "notes.txt").read_text() == "v2\n"
    assert (tmp_path / "scratch.md").read_text() == "mine\n"
    assert not (tmp_path / "yarn.lock").exists()


def test_vetoed_commit_rolls_back_passing_siblings(tmp_path: Path, init_git_repo) -> None:
    repo = tmp_path / "repo"
    _commit_tree(
        repo,
        {
            "package.json": '{"name":"root","packageManager":"npm@10.0.0"}\n',
            "package-lock.json": "{}\n",
            "Dockerfile": "FROM alpine:3.19\n",
        },
        init_git_repo,
    )

    def _apply(ws: Workspace) -> None:
        if ws.manager == "npm":
            (repo / "package-lock.json").write_text('{"bumped":true}\n')
        else:
            (repo / "Dockerfile").write_text("FROM alpine:3.20\n")

    def _verify(ws: Workspace, **_kw) -> str | None:
        return "container verify failed" if ws.manager == "container" else None

    register_all()
    with (
        patch("repo_ops_upgrade.orchestration.ecosystems.apply_workspace", side_effect=_apply),
        patch("repo_ops_upgrade.orchestration.dispatch_remediations"),
        patch("repo_ops_upgrade.orchestration.verify_mod.verify_workspace", side_effect=_verify),
        # A fleet sweep (no roots named) is what reverts a vetoed repo.
        patch("repo_ops_core.cli.DEFAULT_ROOTS", [str(repo)]),
        patch("repo_ops_upgrade.run.DEFAULT_ROOTS", [str(repo)]),
    ):
        rc = main_apply([])

    assert rc == 1
    assert _git(repo, "status", "--porcelain") == ""
    assert _git(repo, "rev-list", "--count", "HEAD").strip() == "1"


@pytest.mark.parametrize(
    ("manager", "tree", "cmd_prefix"),
    [
        ("poetry", ".venv", ["poetry", "install", "--sync"]),
        ("pipenv", ".venv", ["pipenv", "sync"]),
        ("rust", "target", ["cargo", "fetch", "--locked"]),
        ("go", "vendor", ["go", "mod", "vendor"]),
        ("deno", "node_modules", ["deno", "install", "--frozen"]),
        ("pip-tools", ".venv", ["uv", "pip", "sync"]),
    ],
)
def test_reinstall_from_lockfile_covers_missing_managers(
    tmp_path: Path, make_workspace, manager: str, tree: str, cmd_prefix: list[str]
) -> None:
    from repo_ops_upgrade.post_apply import reinstall_from_lockfile

    (tmp_path / tree).mkdir()
    if manager == "pip-tools":
        (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    ws = make_workspace(tmp_path, manager=manager, ecosystem="py", label="demo")
    calls: list[list[str]] = []

    def _ok(cmd, cwd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_ok):
        assert reinstall_from_lockfile(ws) is True
    assert calls, f"{manager} must reinstall from the restored lockfile"
    assert calls[0][: len(cmd_prefix)] == cmd_prefix
