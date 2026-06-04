"""Lockfile parser unit tests."""

import tempfile
from pathlib import Path

from repo_ops_lockfiles.parsers import (
    parse_package_json_direct,
    versions_bun_lock,
    versions_npm_lock,
    versions_pnpm_lock,
    versions_yarn_lock,
)


def test_parse_package_json_direct_counts_all_sections():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "package.json"
        p.write_text(
            '{"dependencies":{"a":"1"},"devDependencies":{"b":"2"},'
            '"peerDependencies":{"c":"3"},"optionalDependencies":{"d":"4"}}'
        )
        assert parse_package_json_direct(p) == 4


def test_parse_package_json_direct_handles_missing_or_invalid():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "package.json"
        p.write_text("not json")
        assert parse_package_json_direct(p) == 0
        (p.parent / "empty.json").write_text("{}")
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
    content = """foo@^1.0.0:
  version "1.2.3"

@scope/bar@^2.0.0:
  version "2.3.4"
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "yarn.lock"
        p.write_text(content)
        res = versions_yarn_lock(p)
        assert "foo@1.2.3" in res
        assert any("bar@2.3.4" in x for x in res)


def test_versions_pnpm_lock_parses():
    content = """lockfileVersion: '6.0'

packages:
  foo@1.2.3:
    resolution: {integrity: ...}
  @scope/bar@2.3.4:
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pnpm-lock.yaml"
        p.write_text(content)
        assert versions_pnpm_lock(p) == {"foo@1.2.3", "@scope/bar@2.3.4"}


def test_versions_pnpm_lock_path_notation():
    """pnpm v5/older path-notation entries must parse to name@version (not empty)."""
    content = """lockfileVersion: 5

packages:
  /lodash/4.17.21:
    resolution: {integrity: sha512-abc}
  /@babel/core/7.22.0:
    resolution: {integrity: sha512-xyz}
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pnpm-lock.yaml"
        p.write_text(content)
        assert versions_pnpm_lock(p) == {"lodash@4.17.21", "@babel/core@7.22.0"}


def test_versions_bun_lock_json_and_fallback():
    content = """{
  "packages": {
    "foo@1.2.3": ["foo@1.2.3"]
  }
}"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bun.lock"
        p.write_text(content)
        assert versions_bun_lock(p) == {"foo@1.2.3"}
