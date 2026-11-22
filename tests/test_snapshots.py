"""Tests for lockfile snapshot diffing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from repo_ops_lockfiles.constants import DENO_LOCKFILES, PY_LOCKFILES
from repo_ops_lockfiles.snapshots import (
    _diff_lock_text,
    diff_workspace_snapshots,
    resolved_version_from_node_modules,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)


def test_resolved_version_bare_scope_does_not_crash() -> None:
    """Bare-scope name like '@types' must not raise ValueError."""
    with tempfile.TemporaryDirectory() as tmp:
        mdir = Path(tmp)
        # No node_modules present; function must return None without crashing.
        result = resolved_version_from_node_modules(mdir, "@types")
        assert result is None


def test_diff_lock_text_detects_in_range_version_bump() -> None:
    """A same-range bump (4.17.20→4.17.21) must produce a Change."""
    before = """\
"lodash@npm:^4.17.0":
  version "4.17.20"
  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.20.tgz"
  integrity sha512-abc
"""
    after = """\
"lodash@npm:^4.17.0":
  version "4.17.21"
  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz"
  integrity sha512-xyz
"""
    changes = _diff_lock_text(before, after)
    assert len(changes) == 1
    assert changes[0].name == "lodash"
    assert changes[0].from_ver == "4.17.20"
    assert changes[0].to == "4.17.21"


def test_diff_lock_text_no_changes_when_identical() -> None:
    content = """\
"react@npm:^18.0.0":
  version "18.2.0"
  resolved "https://registry.yarnpkg.com/react/-/react-18.2.0.tgz"
"""
    changes = _diff_lock_text(content, content)
    assert changes == []


def test_diff_lock_text_scoped_package() -> None:
    before = """\
"@babel/core@npm:^7.0.0":
  version "7.22.0"
"""
    after = """\
"@babel/core@npm:^7.0.0":
  version "7.23.0"
"""
    changes = _diff_lock_text(before, after)
    assert len(changes) == 1
    assert changes[0].name == "@babel/core"
    assert changes[0].from_ver == "7.22.0"
    assert changes[0].to == "7.23.0"


def test_snapshot_workspace_files_pdm(tmp_path: Path) -> None:
    lock = tmp_path / "pdm.lock"
    lock.write_text('[[package]]\nname = "x"\nversion = "1"\n', encoding="utf-8")
    snap = snapshot_workspace_files(tmp_path, "pdm")
    assert lock in snap
    assert "pdm.lock" in PY_LOCKFILES


def test_snapshot_workspace_files_pipenv(tmp_path: Path) -> None:
    lock = tmp_path / "Pipfile.lock"
    lock.write_text("{}", encoding="utf-8")
    snap = snapshot_workspace_files(tmp_path, "pipenv")
    assert lock in snap


def test_snapshot_workspace_files_pip_tools_includes_requirements_in(tmp_path: Path) -> None:
    req_in = tmp_path / "requirements.in"
    req_txt = tmp_path / "requirements.txt"
    req_in.write_text("requests\n", encoding="utf-8")
    req_txt.write_text("requests==2.0.0\n", encoding="utf-8")
    snap = snapshot_workspace_files(tmp_path, "pip-tools")
    assert req_in in snap
    assert req_txt in snap


def test_snapshot_workspace_files_deno(tmp_path: Path) -> None:
    lock = tmp_path / "deno.lock"
    lock.write_text("{}", encoding="utf-8")
    snap = snapshot_workspace_files(tmp_path, "deno")
    assert lock in snap
    assert DENO_LOCKFILES == ("deno.lock",)


def test_diff_workspace_snapshots_coarse_pdm_pipenv_deno(tmp_path: Path) -> None:
    """pdm.lock / Pipfile.lock / deno.lock byte changes emit coarse Change rows."""
    for name, manager in (
        ("pdm.lock", "pdm"),
        ("Pipfile.lock", "pipenv"),
        ("deno.lock", "deno"),
    ):
        p = tmp_path / name
        changes = diff_workspace_snapshots({p: b"old"}, {p: b"new"}, manager)
        assert any(c.name == name for c in changes), f"expected coarse change for {name}"


def test_restore_snapshot_covers_workspace_member_manifest(tmp_path: Path) -> None:
    """A monorepo revert restores member manifests the apply edited, not only the root's."""
    (tmp_path / "package.json").write_text('{"workspaces": ["apps/*"]}')
    member = tmp_path / "apps" / "web" / "package.json"
    member.parent.mkdir(parents=True)
    member.write_text('{"dependencies": {"next": "16.3.0"}}')
    snap = snapshot_workspace_files(tmp_path, "bun")
    member.write_text('{"dependencies": {"next": "16.3.5"}}')
    restore_workspace_snapshot(tmp_path, snap, "bun")
    assert "16.3.0" in member.read_text()


def test_snapshot_captures_a_link_that_stays_inside_the_repo(tmp_path: Path) -> None:
    """The repo, not the workspace, bounds a snapshot: an in-repo link is ours to restore."""
    repo = tmp_path / "repo"
    ws = repo / "pkg"
    ws.mkdir(parents=True)
    (repo / ".git").mkdir()
    shared = repo / "shared.txt"
    shared.write_text("foo==1.0\n", encoding="utf-8")
    (ws / "requirements.txt").symlink_to(shared)

    snap = snapshot_workspace_files(ws, "pip-tools")

    assert snap[ws / "requirements.txt"] == b"foo==1.0\n"


def test_snapshot_skips_a_link_leaving_the_repo(tmp_path: Path) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    (ws / ".git").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("foo==1.0\n", encoding="utf-8")
    (ws / "requirements.txt").symlink_to(outside)

    assert snapshot_workspace_files(ws, "pip-tools") == {}


def test_structured_lockfiles_name_the_packages_that_moved(tmp_path: Path) -> None:
    from repo_ops_lockfiles.snapshots import diff_workspace_snapshots

    uv_before = b'[[package]]\nname = "httpx"\nversion = "0.25.2"\n'
    uv_after = b'[[package]]\nname = "httpx"\nversion = "0.28.1"\n'
    go_before = b"module x\n\ngo 1.22\n\nrequire (\n\tgithub.com/spf13/cobra v1.5.0\n)\n"
    go_after = b"module x\n\ngo 1.22\n\nrequire (\n\tgithub.com/spf13/cobra v1.7.0\n)\n"
    npm_before = b'{"packages": {"": {}, "node_modules/semver": {"version": "6.0.0"}}}'
    npm_after = b'{"packages": {"": {}, "node_modules/semver": {"version": "7.8.6"}}}'

    def moves(name: str, before: bytes, after: bytes, manager: str) -> list[tuple[str, str, str]]:
        path = tmp_path / name
        changes = diff_workspace_snapshots({path: before}, {path: after}, manager)
        return [(c.name, c.from_ver, c.to) for c in changes]

    assert moves("uv.lock", uv_before, uv_after, "uv") == [("httpx", "0.25.2", "0.28.1")]
    assert moves("go.mod", go_before, go_after, "go") == [
        ("github.com/spf13/cobra", "v1.5.0", "v1.7.0")
    ]
    assert moves("go.sum", b"a\n", b"b\n", "go") == []
    assert moves("package-lock.json", npm_before, npm_after, "npm") == [
        ("semver", "6.0.0", "7.8.6")
    ]
