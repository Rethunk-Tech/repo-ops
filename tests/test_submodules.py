"""Submodule umbrella workflow (#13)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_upgrade import submodules
from repo_ops_upgrade.run import main_plan


def _write_gitmodules(parent: Path, path: str, url: str) -> None:
    (parent / ".gitmodules").write_text(
        f'[submodule "{path}"]\n\tpath = {path}\n\turl = {url}\n',
        encoding="utf-8",
    )


def test_parse_gitmodules_and_discover(tmp_path: Path, init_git_repo) -> None:
    parent = tmp_path / "umbrella"
    child = parent / "libs" / "child"
    child.mkdir(parents=True)
    init_git_repo(parent)
    init_git_repo(child)
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


def test_plan_json_includes_submodules(tmp_path: Path, capsys, init_git_repo) -> None:
    parent = tmp_path / "umbrella"
    child = parent / "pkg"
    child.mkdir(parents=True)
    init_git_repo(parent)
    init_git_repo(child)
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


def test_sort_bundles_self_referential_link_terminates(tmp_path: Path) -> None:
    """A self-referential SubmoduleLink (git_repo == parent) must not hang.

    This is the regression case for issue #36: an uninitialized submodule
    causes git_repo_for to walk up and return the parent repo, producing a
    link where git_repo == parent.  The fix-point loop in
    sort_bundles_children_first would previously spin forever on such a link.
    """
    p = (tmp_path / "repo").resolve()
    p.mkdir()
    # Construct the pathological link directly (no git init needed).
    self_link = submodules.SubmoduleLink(path="sub", git_repo=p, parent=p)
    # Must terminate (would hang before the fix) and must exclude the bad link.
    ordered = submodules.sort_bundles_children_first([p], [self_link])
    assert ordered == [p]


def test_discover_skips_uninitialized_submodule(tmp_path: Path, init_git_repo) -> None:
    """A registered but uninitialized submodule (no .git) is excluded."""
    parent = tmp_path / "umbrella"
    parent.mkdir()
    init_git_repo(parent)
    # Register a submodule path that exists as a plain dir (no git init).
    uninit_dir = parent / "sub"
    uninit_dir.mkdir()
    _write_gitmodules(parent, "sub", "https://example.com/sub.git")

    links = submodules.discover_submodules_under_roots([str(parent)], flat=True)
    assert links == []


def test_dirty_submodule_pointer_hint(tmp_path: Path, init_git_repo) -> None:
    parent = tmp_path / "umbrella"
    child = parent / "pkg"
    child.mkdir(parents=True)
    init_git_repo(parent)
    init_git_repo(child)
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


def test_find_gitmodules_prunes_skip_dirs(tmp_path: Path, init_git_repo) -> None:
    """Regression for #43: _find_gitmodules must not descend into SKIP_DIRS
    (e.g. node_modules) and must not register a .gitmodules found inside them."""
    root = tmp_path / "repo"
    root.mkdir()
    init_git_repo(root)
    # Legitimate .gitmodules at the repo root.
    _write_gitmodules(root, "sub", "https://example.com/sub.git")
    # A .gitmodules inside node_modules — should be ignored.
    nm = root / "node_modules" / "some-pkg"
    nm.mkdir(parents=True)
    (nm / ".gitmodules").write_text(
        '[submodule "evil"]\n\tpath = evil\n\turl = https://evil.example.com/evil.git\n',
        encoding="utf-8",
    )

    from repo_ops_upgrade.submodules import _find_gitmodules

    found = _find_gitmodules(root)
    paths = [str(p) for p in found]
    assert any(p.endswith(".gitmodules") and "node_modules" not in p for p in paths), (
        f"legitimate .gitmodules not found; got {paths}"
    )
    assert not any("node_modules" in p for p in paths), (
        f"node_modules/.gitmodules must be pruned; got {paths}"
    )


def test_discover_rejects_self_referential_dot_path(
    tmp_path: Path, init_git_repo
) -> None:
    """Regression for #47: a .gitmodules entry with path='.' produces
    child_dir == parent; the existing guard (child_repo.resolve() != child_dir)
    passes because git_repo_for('.') == the repo itself, so an explicit check
    for child_repo.resolve() == parent is required to reject it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    # Write a .gitmodules with path = "." (self-referential).
    (repo / ".gitmodules").write_text(
        '[submodule "self"]\n\tpath = .\n\turl = https://example.com/self.git\n',
        encoding="utf-8",
    )

    links = submodules.discover_submodules_under_roots([str(repo)], flat=True)
    assert links == [], (
        f"self-referential path='.' submodule must be rejected; got {links}"
    )
