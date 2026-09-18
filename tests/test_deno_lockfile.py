"""Deno lockfile parser across format versions v2-v4 (fixtures in tests/fixtures/)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_ops_lockfiles.parsers import versions_deno_lock

FIXTURE_DIR = Path(__file__).parent / "fixtures"

_DENO_FIXTURES = ("deno_v2.lock", "deno_v3.lock", "deno_v4.lock")


def _copy_lock_fixture(tmp_path: Path, fixture_name: str) -> Path:
    """Copy a canonical on-disk Deno lock fixture into *tmp_path*."""
    src = FIXTURE_DIR / fixture_name
    if not src.exists():
        pytest.skip(f"fixture file not found: {fixture_name}")
    dest = tmp_path / fixture_name
    dest.write_bytes(src.read_bytes())
    return dest


def test_v3_specifiers_captured(tmp_path: Path) -> None:
    """v3: packages.specifiers → resolved specifier strings are captured."""
    versions = versions_deno_lock(_copy_lock_fixture(tmp_path, "deno_v3.lock"))
    assert "jsr:@std/assert@0.221.0" in versions
    assert "npm:chalk@5.2.0" in versions
    assert len(versions) == 2


def test_v4_top_level_specifiers_captured(tmp_path: Path) -> None:
    """v4: top-level specifiers → resolved specifier strings are captured."""
    versions = versions_deno_lock(_copy_lock_fixture(tmp_path, "deno_v4.lock"))
    assert "jsr:@std/assert@1.0.0" in versions
    assert "npm:chalk@5.3.0" in versions
    assert len(versions) == 2


def test_v2_npm_captured(tmp_path: Path) -> None:
    """v2: top-level npm dict keys are captured as npm:name@version."""
    versions = versions_deno_lock(_copy_lock_fixture(tmp_path, "deno_v2.lock"))
    assert "npm:chalk@5.1.0" in versions
    assert len(versions) == 1


def test_empty_lockfile_returns_empty_set(tmp_path: Path) -> None:
    """An empty/no-content lockfile returns an empty set (no crash)."""
    p = tmp_path / "deno.lock"
    p.write_text("{}")
    assert versions_deno_lock(p) == set()


def test_malformed_lockfile_returns_empty_set(tmp_path: Path) -> None:
    """A malformed JSON lockfile returns an empty set (no crash)."""
    p = tmp_path / "deno.lock"
    p.write_text("not valid json")
    assert versions_deno_lock(p) == set()
