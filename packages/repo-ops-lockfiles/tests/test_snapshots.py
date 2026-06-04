"""Tests for lockfile snapshot diffing (#56 — in-range yarn.lock bumps detected)."""

from __future__ import annotations

from repo_ops_lockfiles.snapshots import _diff_lock_text


def test_diff_lock_text_detects_in_range_version_bump() -> None:
    """#56 — a same-range bump (4.17.20→4.17.21) must produce a Change."""
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
