"""Regression tests for scripts/set_version.py (release version tool)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_version.py"
_spec = importlib.util.spec_from_file_location("set_version", _SCRIPT)
assert _spec is not None and _spec.loader is not None
set_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(set_version)

_FIXTURE = (
    "[build-system]\n"
    'requires = ["uv_build>=0.11.16,<0.12.0"]\n'
    'build-backend = "uv_build"\n'
    "\n"
    "[project]\n"
    'name = "demo"\n'
    'version = "0.7.0"\n'
    "\n"
    "[tool.foo]\n"
    'version = "9.9.9"\n'
)


def test_set_version_scopes_to_project_and_bumps(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(_FIXTURE, encoding="utf-8")

    # Reads the [project] version, never the [build-system]/[tool] lines.
    assert set_version.read_version(pyproject) == "0.7.0"

    # Bump arithmetic for all three parts.
    assert set_version.bump_version("0.7.0", "patch") == "0.7.1"
    assert set_version.bump_version("0.7.9", "minor") == "0.8.0"
    assert set_version.bump_version("1.2.3", "major") == "2.0.0"

    # Writing rewrites only [project].version; siblings stay byte-for-byte.
    assert set_version.write_version(pyproject, "0.7.1") is True
    assert set_version.read_version(pyproject) == "0.7.1"
    text = pyproject.read_text(encoding="utf-8")
    assert 'version = "9.9.9"' in text  # [tool.foo] untouched
    assert 'requires = ["uv_build>=0.11.16,<0.12.0"]' in text  # build-system untouched

    # Idempotent: no rewrite when already at the target version.
    assert set_version.write_version(pyproject, "0.7.1") is False


def test_manifests_count_matches_repo_layout() -> None:
    """manifests() must include root + all packages/*/pyproject.toml.

    Guards the release script against silent miscount if packages/ is
    reorganised (added, removed, or renamed without updating set_version).
    """
    repo_root = Path(__file__).resolve().parents[1]
    expected_count = 1 + len(sorted((repo_root / "packages").glob("*/pyproject.toml")))
    found = set_version.manifests()
    assert len(found) == expected_count, (
        f"manifests() returned {len(found)} paths; "
        f"expected {expected_count} (1 root + {expected_count - 1} packages)"
    )


_PRERELEASE_FIXTURE = '[project]\nname = "demo"\nversion = "0.8.0rc1"\n'

_NORMAL_FIXTURE = '[project]\nname = "normal"\nversion = "0.8.0"\n'


def test_apply_warns_and_counts_unparseable_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_apply must emit a stderr warning and return 1 for non-standard versions.

    Pre-release strings like "0.8.0rc1" do not match VERSION_RE, so
    read_version returns None.  The manifest must be reported on stderr,
    counted as skipped (not silently dropped), and must NOT be rewritten.
    """
    # Two manifests: one parseable, one pre-release.
    root_toml = tmp_path / "pyproject.toml"
    root_toml.write_text(_NORMAL_FIXTURE, encoding="utf-8")
    sub_dir = tmp_path / "packages" / "sub"
    sub_dir.mkdir(parents=True)
    prerelease_toml = sub_dir / "pyproject.toml"
    prerelease_toml.write_text(_PRERELEASE_FIXTURE, encoding="utf-8")

    # Patch ROOT so path.relative_to(ROOT) works inside _apply.
    monkeypatch.setattr(set_version, "ROOT", tmp_path)

    rc = set_version._apply([root_toml, prerelease_toml], "0.9.0", dry_run=False)

    captured = capsys.readouterr()
    # Must exit 1 because a manifest was skipped.
    assert rc == 1
    # stderr must name the skipped manifest.
    assert "pyproject.toml" in captured.err
    assert "skipped" in captured.err
    # The skipped manifest must not be rewritten.
    assert "0.8.0rc1" in prerelease_toml.read_text(encoding="utf-8")
    # All-or-nothing: parseable manifest must not change when any peer is skipped.
    assert set_version.read_version(root_toml) == "0.8.0"
    # Summary line must not appear when the run aborts before writes.
    assert "manifest(s)" not in captured.out


def test_show_exits_1_on_unparseable_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """show-mode must exit 1 whenever any manifest version cannot be parsed.

    Covers two sub-cases:
    1. Partial skip: one parseable manifest + one pre-release → exit 1, version shown.
    2. All skipped: only pre-release manifests → exit 1, "(none)" summary.
    """
    monkeypatch.setattr(set_version, "ROOT", tmp_path)
    root_toml = tmp_path / "pyproject.toml"
    sub_dir = tmp_path / "packages" / "sub"
    sub_dir.mkdir(parents=True)
    prerelease_toml = sub_dir / "pyproject.toml"
    prerelease_toml.write_text(_PRERELEASE_FIXTURE, encoding="utf-8")

    # Case 1: partial skip — root is parseable, sub is pre-release.
    root_toml.write_text(_NORMAL_FIXTURE, encoding="utf-8")
    rc = set_version.main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "skipped" in captured.err
    assert "current version: 0.8.0" in captured.out

    # Case 2: all skipped — root is also pre-release.
    root_toml.write_text(_PRERELEASE_FIXTURE, encoding="utf-8")
    rc = set_version.main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "skipped" in captured.err
    assert "current version: (none)" in captured.out
