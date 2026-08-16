"""Lockfile parser unit tests."""

import tempfile
from pathlib import Path

from repo_ops_lockfiles.parsers import (
    parse_package_json_direct,
    versions_bun_lock,
    versions_cargo_lock,
    versions_deno_lock,
    versions_go_sum,
    versions_npm_lock,
    versions_pnpm_lock,
    versions_requirements_txt,
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


def test_versions_go_sum_strips_go_mod_suffix():
    content = "golang.org/x/net v0.17.0 h1:abc=\ngolang.org/x/net v0.17.0/go.mod h1:xyz=\n"
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "go.sum"
        p.write_text(content)
        assert versions_go_sum(p) == {"golang.org/x/net@v0.17.0"}


def test_versions_cargo_lock_ignores_nested_dependency_tables():
    content = """\
[[package]]
name = "serde"
version = "1.0.197"
dependencies = ["serde_derive 1.0.197"]

[[package]]
name = "tokio"
version = "1.36.0"

[metadata]
"serde 1.0.197" = "checksum"
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "Cargo.lock"
        p.write_text(content)
        assert versions_cargo_lock(p) == {"serde@1.0.197", "tokio@1.36.0"}


def test_versions_requirements_txt_parses_pins_and_extras():
    content = """\
requests==2.31.0
flask[async]==3.0.0; python_version >= "3.9"
click>=8.1
# comment
-r other.txt
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "requirements.txt"
        p.write_text(content)
        assert versions_requirements_txt(p) == {
            "requests@2.31.0",
            "flask@3.0.0",
        }


def test_versions_deno_lock_v3_packages_jsr():
    content = """\
{
  "version": "3",
  "packages": {
    "jsr": {
      "@scope/pkg@1.0.0": {"integrity": "sha256-example"}
    }
  }
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "deno.lock"
        p.write_text(content)
        assert versions_deno_lock(p) == {"jsr:@scope/pkg@1.0.0"}


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


def test_versions_pnpm_lock_v5_peer_suffix_not_swallowed():
    """v5 path lines with @peer-suffix must parse correctly via _RE_PATH — closes #53.

    Before the fix _RE_AT matched these first and emitted a garbled token.
    A plain name@version line must still parse correctly too.
    """
    content = """lockfileVersion: 5

packages:
  /react-dom/18.2.0_react@18.2.0:
    resolution: {integrity: sha512-abc}
  /lodash/4.17.21:
    resolution: {integrity: sha512-xyz}
  lodash@4.17.21:
    resolution: {integrity: sha512-abc}
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pnpm-lock.yaml"
        p.write_text(content)
        result = versions_pnpm_lock(p)
        # The peer-suffix path line must start with "react-dom@" (not "react-dom/")
        assert any(t.startswith("react-dom@18.2.0") for t in result), (
            f"peer-suffix path line not parsed correctly; got: {result}"
        )
        # Plain name@version must still parse
        assert "lodash@4.17.21" in result, f"plain name@version line broken; got: {result}"
        # Path-notation without peer suffix must still parse
        assert "lodash@4.17.21" in result


def test_versions_pnpm_lock_v6_peer_suffix_stripped():
    """#79 — pnpm v6 peer-dep suffix must not appear in emitted token."""
    content = """lockfileVersion: '6.0'

packages:
  /@babel/core@7.21.0(supports-color@8.1.1):
    resolution: {integrity: sha512-abc}
  foo@1.0.0:
    resolution: {integrity: sha512-xyz}
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pnpm-lock.yaml"
        p.write_text(content)
        result = versions_pnpm_lock(p)
        assert result == {"@babel/core@7.21.0", "foo@1.0.0"}
        assert not any("supports-color" in t for t in result)


def test_versions_yarn_lock_skips_metadata_block():
    """#79 — Berry __metadata preamble must not emit a bogus __metadata@6 token."""
    content = """\
__metadata:
  version: 6
  cacheKey: 8

foo@^1.0.0:
  version "1.2.3"
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "yarn.lock"
        p.write_text(content)
        result = versions_yarn_lock(p)
        assert "foo@1.2.3" in result
        assert not any(t.startswith("__metadata") for t in result)


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
