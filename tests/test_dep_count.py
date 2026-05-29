"""Lightweight unit tests for pure parsing/counting helpers in dep_counting.

These tests use in-memory strings/paths and avoid any real package manager
invocation or large filesystem scans.
"""

from pathlib import Path
import tempfile
import pytest

from repo_ops.dep_counting import (
    parse_package_json_direct,
    versions_npm_lock,
    versions_yarn_lock,
    versions_pnpm_lock,
    versions_bun_lock,
    count_rust,
    count_python,
)


def test_parse_package_json_direct_counts_all_sections():
    data = {
        "dependencies": {"a": "1"},
        "devDependencies": {"b": "2"},
        "peerDependencies": {"c": "3"},
        "optionalDependencies": {"d": "4"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "package.json"
        p.write_text('{"dependencies":{"a":"1"},"devDependencies":{"b":"2"},"peerDependencies":{"c":"3"},"optionalDependencies":{"d":"4"}}')
        assert parse_package_json_direct(p) == 4


def test_parse_package_json_direct_handles_missing_or_invalid():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "package.json"
        p.write_text('not json')
        assert parse_package_json_direct(p) == 0
        (p.parent / "empty.json").write_text('{}')
        assert parse_package_json_direct(p.parent / "empty.json") == 0


def test_versions_npm_lock_v2_and_v1_fallback():
    v2 = {
        "packages": {
            "node_modules/foo": {"name": "foo", "version": "1.2.3"},
            "": {"name": "root", "version": "0.0.0"},
        }
    }
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "package-lock.json"
        import json
        p.write_text(json.dumps(v2))
        assert versions_npm_lock(p) == {"foo@1.2.3"}


def test_versions_yarn_lock_parses_basic():
    content = '''foo@^1.0.0:
  version "1.2.3"

@scope/bar@^2.0.0:
  version "2.3.4"
'''
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "yarn.lock"
        p.write_text(content)
        res = versions_yarn_lock(p)
        assert "foo@1.2.3" in res
        assert " @scope/bar@2.3.4" not in res  # name extraction
        assert any("bar@2.3.4" in x for x in res)


def test_versions_pnpm_lock_parses():
    content = '''lockfileVersion: '6.0'

packages:
  foo@1.2.3:
    resolution: {integrity: ...}
  @scope/bar@2.3.4:
'''
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pnpm-lock.yaml"
        p.write_text(content)
        assert versions_pnpm_lock(p) == {"foo@1.2.3", "@scope/bar@2.3.4"}


def test_versions_bun_lock_json_and_fallback():
    content = '''{
  "packages": {
    "foo@1.2.3": ["foo@1.2.3"]
  }
}'''
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bun.lock"
        p.write_text(content)
        assert versions_bun_lock(p) == {"foo@1.2.3"}


def test_count_rust_and_python_minimal(tmp_path: Path):
    # Rust
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n[dependencies]\na = "1"')
    (tmp_path / "Cargo.lock").write_text('[[package]]\nname = "a"\nversion = "1.0"\n')
    d, v = count_rust(tmp_path)
    assert d == 1
    assert "a@1.0" in v

    # Python pyproject only
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["b"]')
    d2, v2, tag = count_python(tmp_path)
    assert d2 >= 1
    assert "pyproject" in tag
