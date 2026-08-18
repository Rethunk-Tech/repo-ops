"""Tests for lockfile snapshot diffing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from repo_ops_lockfiles.constants import DENO_LOCKFILES, PY_LOCKFILES
from repo_ops_lockfiles.snapshots import (
    _diff_lock_text,
    diff_workspace_snapshots,
    resolved_version_from_node_modules,
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
