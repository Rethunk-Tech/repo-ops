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
