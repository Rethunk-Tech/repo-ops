"""Regression tests for overrides_audit protocol-pin false-positive (issue #27).

workspace:*, file:, and other protocol pins must never produce a stale warning.
A genuinely stale semver override (pinned < resolved) still must fire.
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_js.overrides import overrides_audit


def _write_pkg_json(mdir: Path, overrides: dict) -> None:
    pkg = {"name": "test", "version": "1.0.0", "overrides": overrides}
    (mdir / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


def _install_pkg(mdir: Path, pkg_name: str, version: str) -> None:
    """Simulate a flat node_modules install so resolved_version_from_node_modules finds it."""
    pkg_dir = mdir / "node_modules" / pkg_name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.json").write_text(
        json.dumps({"name": pkg_name, "version": version}),
        encoding="utf-8",
    )


def test_workspace_star_pin_produces_no_stale_warning(tmp_path: Path) -> None:
    """workspace:* override must not fire any stale warning (issue #27)."""
    _write_pkg_json(tmp_path, {"@my/shared": "workspace:*"})
    results = overrides_audit(tmp_path, "pnpm")
    stale = [r for r in results if "stale" in r.get("detail", "")]
    assert stale == [], f"unexpected stale warnings: {stale}"


def test_file_protocol_pin_produces_no_stale_warning(tmp_path: Path) -> None:
    """file:../shared override must not fire any stale warning (issue #27)."""
    _write_pkg_json(tmp_path, {"my-lib": "file:../shared"})
    results = overrides_audit(tmp_path, "npm")
    stale = [r for r in results if "stale" in r.get("detail", "")]
    assert stale == [], f"unexpected stale warnings: {stale}"


def test_other_protocol_pins_produce_no_stale_warning(tmp_path: Path) -> None:
    """link:, portal:, git+, github:, https: overrides must all be skipped."""
    _write_pkg_json(
        tmp_path,
        {
            "link-pkg": "link:../local",
            "portal-pkg": "portal:../local",
            "git-pkg": "git+https://github.com/org/repo.git#main",
            "github-pkg": "github:org/repo#abc1234",
            "https-pkg": "https://example.com/pkg.tgz",
        },
    )
    results = overrides_audit(tmp_path, "npm")
    stale = [r for r in results if "stale" in r.get("detail", "")]
    assert stale == [], f"unexpected stale warnings: {stale}"


def test_semver_stale_override_still_fires(tmp_path: Path) -> None:
    """A semver override pinned to an older version than what's installed fires a stale warning."""
    # pin to 1.0.0, but node_modules has 2.0.0 — genuinely stale
    _write_pkg_json(tmp_path, {"lodash": "1.0.0"})
    _install_pkg(tmp_path, "lodash", "2.0.0")
    results = overrides_audit(tmp_path, "npm")
    stale = [r for r in results if "stale" in r.get("detail", "")]
    assert len(stale) == 1, f"expected exactly one stale warning, got: {results}"
    assert "lodash" in stale[0]["detail"]


def test_npm_and_catalog_protocol_pins_produce_no_warning(tmp_path: Path) -> None:
    """npm: alias and catalog: protocol pins must produce no warnings — closes #50."""
    _write_pkg_json(
        tmp_path,
        {
            "react": "npm:preact@^10.0.0",
            "lodash": "catalog:default",
            "axios": "http://example.com/axios.tgz",
            "my-patch": "patch:my-pkg@1.0.0#./patches/my-pkg.patch",
        },
    )
    results = overrides_audit(tmp_path, "pnpm")
    assert results == [], f"expected no warnings for protocol pins, got: {results}"


def test_range_override_in_range_resolution_produces_no_stale_warning(
    tmp_path: Path,
) -> None:
    """^1.2.3 override with tree at 1.2.5 (in-range) must not fire stale — closes #78.

    Also asserts that an exact-pin mismatch (1.2.3 pinned, 1.2.5 resolved) still
    fires a stale warning, proving the equality check is not simply disabled.
    """
    # Case 1: range pin — 1.2.5 satisfies ^1.2.3, must NOT warn
    _write_pkg_json(tmp_path, {"semver-range-pkg": "^1.2.3"})
    _install_pkg(tmp_path, "semver-range-pkg", "1.2.5")
    results = overrides_audit(tmp_path, "npm")
    stale = [r for r in results if "stale" in r.get("detail", "")]
    assert stale == [], f"range pin with in-range resolution should not warn: {stale}"

    # Case 2: exact pin — 1.2.3 pinned but 1.2.5 resolved — MUST still warn
    mdir2 = tmp_path / "exact"
    mdir2.mkdir()
    _write_pkg_json(mdir2, {"semver-exact-pkg": "1.2.3"})
    _install_pkg(mdir2, "semver-exact-pkg", "1.2.5")
    results2 = overrides_audit(mdir2, "npm")
    stale2 = [r for r in results2 if "stale" in r.get("detail", "")]
    assert len(stale2) == 1, f"exact-pin mismatch must still warn: {results2}"
    assert "semver-exact-pkg" in stale2[0]["detail"]


def test_overrides_audit_scans_workspace_member_manifests(tmp_path: Path) -> None:
    """Monorepo pins in packages/*/package.json must surface (F150)."""
    root = tmp_path
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "root",
                "private": True,
                "workspaces": ["packages/*"],
            }
        ),
        encoding="utf-8",
    )
    member = root / "packages" / "app"
    member.mkdir(parents=True)
    (member / "package.json").write_text(
        json.dumps(
            {
                "name": "app",
                "overrides": {"lodash": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    _install_pkg(root, "lodash", "2.0.0")
    results = overrides_audit(root, "npm")
    stale = [r for r in results if "stale" in r.get("detail", "")]
    assert len(stale) == 1, f"expected member override stale warning, got: {results}"
    assert "[npm]" in stale[0]["detail"]
    assert "lodash" in stale[0]["detail"]
