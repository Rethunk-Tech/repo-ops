"""Submodule umbrella workflow (#13)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_upgrade import submodules
from repo_ops_upgrade.run import main_plan


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _write_gitmodules(parent: Path, path: str, url: str) -> None:
    (parent / ".gitmodules").write_text(
        f'[submodule "{path}"]\n\tpath = {path}\n\turl = {url}\n',
        encoding="utf-8",
    )


def test_parse_gitmodules_and_discover(tmp_path: Path) -> None:
    parent = tmp_path / "umbrella"
    child = parent / "libs" / "child"
    child.mkdir(parents=True)
    _init_git_repo(parent)
    _init_git_repo(child)
    _write_gitmodules(parent, "libs/child", "https://example.com/child.git")

    links = submodules.discover_submodules_under_roots([str(parent)], flat=True)
    assert len(links) == 1
    assert links[0].path == "libs/child"
    assert links[0].git_repo.resolve() == child.resolve()
    assert links[0].parent.resolve() == parent.resolve()

    payload = submodules.submodules_to_json(links)
    assert payload[0]["path"] == "libs/child"
    assert "git_repo" in payload[0]
    assert "parent" in payload[0]


def test_sort_bundles_children_first(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "sub"
    child.mkdir(parents=True)
    parent_p = parent.resolve()
    child_p = child.resolve()
    links = [
        submodules.SubmoduleLink(
            path="sub",
            git_repo=child_p,
            parent=parent_p,
        )
    ]
    ordered = submodules.sort_bundles_children_first(
        [parent_p, child_p],
        links,
    )
    assert ordered[0] == child_p
    assert ordered[1] == parent_p


def test_plan_json_includes_submodules(tmp_path: Path, capsys) -> None:
    parent = tmp_path / "umbrella"
    child = parent / "pkg"
    child.mkdir(parents=True)
    _init_git_repo(parent)
    _init_git_repo(child)
    _write_gitmodules(parent, "pkg", "https://example.com/pkg.git")
    (child / "package.json").write_text(
        '{"name":"child","packageManager":"npm@10.0.0"}',
        encoding="utf-8",
    )
    (child / "package-lock.json").write_text("{}", encoding="utf-8")

    register_all()

    def _noop_workspace(wr, **_kwargs):
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_noop_workspace,
    ):
        code = main_plan(
            ["--flat", "--json", "--submodules", str(parent)],
        )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "submodules" in payload
    assert len(payload["submodules"]) == 1
    assert payload["submodules"][0]["path"] == "pkg"


def test_dirty_submodule_pointer_hint(tmp_path: Path) -> None:
    parent = tmp_path / "umbrella"
    child = parent / "pkg"
    child.mkdir(parents=True)
    _init_git_repo(parent)
    _init_git_repo(child)
    _write_gitmodules(parent, "pkg", "https://example.com/pkg.git")
    (child / "README.md").write_text("child\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=child, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=child,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "submodule", "add", str(child), "pkg"],
        cwd=parent,
        check=True,
        capture_output=True,
    )
    (child / "README.md").write_text("child v2\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=child, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "bump"],
        cwd=child,
        check=True,
        capture_output=True,
    )

    links = submodules.discover_submodules_under_roots([str(parent)], flat=True)
    hints = submodules.parent_repos_needing_pointer_commit(links)
    assert hints
    assert hints[0]["parent"] == str(parent.resolve())
    assert "pkg" in hints[0]["paths"]
