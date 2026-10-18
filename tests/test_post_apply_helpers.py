"""Post-apply parsers and path/state helpers the commit suite does not hit."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from repo_ops_models import Workspace
from repo_ops_upgrade.post_apply import (
    _workspace_git_pathspec,
    capture_worktree_state,
    collect_changes_after_apply,
    tree_diff,
)


def _ws(path: Path, git_repo: Path | None = None) -> Workspace:
    repo = git_repo if git_repo is not None else path
    return Workspace(path=path, ecosystem="node", manager="npm", git_repo=repo, label="demo")


@pytest.mark.parametrize(
    ("diff", "want_names", "forbid_names"),
    [
        (
            # Nested object inside a dep section must not close the section
            # (brace_depth / dep_section_depth). left-pad is still a dep;
            # scripts.test is not.
            (
                "diff --git a/package.json b/package.json\n"
                "--- a/package.json\n"
                "+++ b/package.json\n"
                "@@ -1,10 +1,12 @@\n"
                " {\n"
                '   "peerDependencies": {\n'
                '     "react": {\n'
                '       "optional": true\n'
                "     },\n"
                '+    "left-pad": "1.3.0"\n'
                "   },\n"
                '   "scripts": {\n'
                '+    "test": "jest"\n'
                "   }\n"
                " }\n"
            ),
            {"left-pad"},
            {"test", "optional", "react"},
        ),
        (
            # optionalDependencies is a dep section; a nested close then a
            # sibling string bump must still be collected.
            (
                "diff --git a/package.json b/package.json\n"
                "--- a/package.json\n"
                "+++ b/package.json\n"
                "@@ -1,8 +1,9 @@\n"
                " {\n"
                '   "optionalDependencies": {\n'
                '     "fsevents": {\n'
                '       "os": "darwin"\n'
                "     },\n"
                '+    "color": "4.2.3"\n'
                "   }\n"
                " }\n"
            ),
            {"color"},
            {"os", "fsevents"},
        ),
    ],
    ids=("nested-peer-then-scripts", "nested-optional-then-string"),
)
def test_collect_changes_nested_dep_objects(
    tmp_path: Path, diff: str, want_names: set[str], forbid_names: set[str]
) -> None:
    ws = _ws(tmp_path)

    def _fake_run(cmd: list[str], cwd: object, **kwargs: object) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            return CompletedProcess(cmd, 0, stdout="package.json\n", stderr="")
        return CompletedProcess(cmd, 0, stdout=diff, stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    names = {c.name for c in changes}
    assert want_names <= names, names
    assert names.isdisjoint(forbid_names), names


@pytest.mark.parametrize(
    ("filename", "diff"),
    [
        (
            "deno.json",
            (
                "diff --git a/deno.json b/deno.json\n"
                "--- a/deno.json\n"
                "+++ b/deno.json\n"
                "@@ -1,6 +1,7 @@\n"
                " {\n"
                '   "imports": {\n'
                '+    "left-pad": "npm:left-pad@1.3.0"\n'
                "   }\n"
                " }\n"
            ),
        ),
        (
            "deno.jsonc",
            (
                "diff --git a/deno.jsonc b/deno.jsonc\n"
                "--- a/deno.jsonc\n"
                "+++ b/deno.jsonc\n"
                "@@ -1,6 +1,7 @@\n"
                " {\n"
                '   "imports": {\n'
                '+    "@std/assert": "jsr:@std/assert@1.0.0"\n'
                "   }\n"
                " }\n"
            ),
        ),
    ],
    ids=("json", "jsonc"),
)
def test_collect_changes_deno_manifest_import_only(
    tmp_path: Path, filename: str, diff: str
) -> None:
    ws = Workspace(path=tmp_path, ecosystem="deno", manager="deno", git_repo=tmp_path, label="demo")

    def _fake_run(cmd: list[str], cwd: object, **kwargs: object) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            return CompletedProcess(cmd, 0, stdout=f"{filename}\n", stderr="")
        return CompletedProcess(cmd, 0, stdout=diff, stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    assert changes, f"expected a Change from {filename} import-map bump"


def test_collect_changes_deno_tasks_only_is_not_a_package(tmp_path: Path) -> None:
    ws = Workspace(path=tmp_path, ecosystem="deno", manager="deno", git_repo=tmp_path, label="demo")
    diff = (
        "diff --git a/deno.json b/deno.json\n"
        "--- a/deno.json\n"
        "+++ b/deno.json\n"
        "@@ -1,6 +1,7 @@\n"
        " {\n"
        '   "tasks": {\n'
        '+    "test": "deno test"\n'
        "   }\n"
        " }\n"
    )

    def _fake_run(cmd: list[str], cwd: object, **kwargs: object) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            return CompletedProcess(cmd, 0, stdout="deno.json\n", stderr="")
        return CompletedProcess(cmd, 0, stdout=diff, stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    assert "test" not in {c.name for c in changes}


def test_collect_changes_cargo_toml_only(tmp_path: Path) -> None:
    ws = Workspace(path=tmp_path, ecosystem="rust", manager="rust", git_repo=tmp_path, label="demo")

    def _fake_run(cmd: list[str], cwd: object, **kwargs: object) -> CompletedProcess[str]:
        if "--name-only" in cmd:
            return CompletedProcess(cmd, 0, stdout="Cargo.toml\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_upgrade.post_apply.run", side_effect=_fake_run):
        changes = collect_changes_after_apply(ws)

    assert any(c.name == "Cargo.toml" for c in changes), changes


@pytest.mark.parametrize(
    ("ws_rel", "repo_rel", "expected"),
    [
        (".", ".", "."),
        ("pkg", ".", "pkg"),
        ("pkg/nested", ".", "pkg/nested"),
        (".", "elsewhere", "."),
    ],
    ids=("root", "subdir", "nested", "not-under-repo"),
)
def test_workspace_git_pathspec(tmp_path: Path, ws_rel: str, repo_rel: str, expected: str) -> None:
    git_repo = tmp_path / "repo" if repo_rel == "elsewhere" else tmp_path
    git_repo.mkdir(parents=True, exist_ok=True)
    ws_path = tmp_path if ws_rel == "." else tmp_path / ws_rel
    if repo_rel == "elsewhere":
        ws_path = tmp_path / "ws"
    ws_path.mkdir(parents=True, exist_ok=True)
    assert _workspace_git_pathspec(_ws(ws_path, git_repo)) == expected


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("none", []),
        ("edit", ["README.md"]),
        ("add", ["extra.txt"]),
        ("not-git", None),
    ],
    ids=("unchanged", "edited-tracked", "untracked", "git-failed"),
)
def test_tree_diff(tmp_path: Path, init_git_repo, mutate: str, expected: list[str] | None) -> None:
    if mutate == "not-git":
        assert tree_diff(tmp_path, {}) is None
        return
    init_git_repo(tmp_path, commit=True, filename="README.md", content="v1\n")
    before = capture_worktree_state(tmp_path)
    assert before is not None
    if mutate == "edit":
        (tmp_path / "README.md").write_text("v2\n", encoding="utf-8")
    elif mutate == "add":
        (tmp_path / "extra.txt").write_text("n\n", encoding="utf-8")
    assert tree_diff(tmp_path, before) == expected
