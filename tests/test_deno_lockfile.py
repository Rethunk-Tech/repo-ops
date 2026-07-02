"""TDD tests for Deno lockfile parser (all format versions).

Fixture spike output (run against tests/fixtures/):
  v3 versions: ['jsr:@std/assert@0.221.0', 'npm:chalk@5.2.0']  (count=2)
  v4 versions: ['jsr:@std/assert@1.0.0', 'npm:chalk@5.3.0']    (count=2)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from repo_ops_lockfiles.parsers import versions_deno_lock

# -- fixtures ----------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _v3_lock(tmp_path: Path) -> Path:
    p = tmp_path / "deno_v3.lock"
    p.write_text(
        json.dumps(
            {
                "version": "3",
                "packages": {
                    "specifiers": {
                        "jsr:@std/assert@^0.221.0": "jsr:@std/assert@0.221.0",
                        "npm:chalk@^5.0": "npm:chalk@5.2.0",
                    },
                    "jsr": {"@std/assert@0.221.0": {"integrity": "fakehash1"}},
                    "npm": {"chalk@5.2.0": {"integrity": "fakehash2"}},
                },
            }
        )
    )
    return p


def _v4_lock(tmp_path: Path) -> Path:
    p = tmp_path / "deno_v4.lock"
    p.write_text(
        json.dumps(
            {
                "version": "4",
                "specifiers": {
                    "jsr:@std/assert@^1.0": "jsr:@std/assert@1.0.0",
                    "npm:chalk@^5.0": "npm:chalk@5.3.0",
                },
                "jsr": {"@std/assert@1.0.0": {"integrity": "fakehash3"}},
                "npm": {"chalk@5.3.0": {"integrity": "fakehash4", "dependencies": {}}},
            }
        )
    )
    return p


def _v2_lock(tmp_path: Path) -> Path:
    p = tmp_path / "deno_v2.lock"
    p.write_text(
        json.dumps(
            {
                "version": "2",
                "npm": {
                    "chalk@5.1.0": {
                        "integrity": "fakehash5",
                        "dependencies": {},
                    }
                },
                "redirects": {},
            }
        )
    )
    return p


# -- tests -------------------------------------------------------------------


def test_v3_specifiers_captured(tmp_path: Path) -> None:
    """v3: packages.specifiers → resolved specifier strings are captured."""
    versions = versions_deno_lock(_v3_lock(tmp_path))
    # Observed spike values
    assert "jsr:@std/assert@0.221.0" in versions  # from specifiers value
    assert "npm:chalk@5.2.0" in versions  # from specifiers value
    assert len(versions) == 2  # no duplicates from packages.npm


def test_v4_top_level_specifiers_captured(tmp_path: Path) -> None:
    """v4: top-level specifiers → resolved specifier strings are captured."""
    versions = versions_deno_lock(_v4_lock(tmp_path))
    # Observed spike values
    assert "jsr:@std/assert@1.0.0" in versions  # from specifiers value
    assert "npm:chalk@5.3.0" in versions  # from specifiers value
    assert len(versions) == 2  # deduped with jsr/npm sections


def test_v4_jsr_section_captured(tmp_path: Path) -> None:
    """v4: top-level jsr section keys are captured as jsr:@scope/pkg@version."""
    versions = versions_deno_lock(_v4_lock(tmp_path))
    assert "jsr:@std/assert@1.0.0" in versions


def test_v4_npm_section_captured(tmp_path: Path) -> None:
    """v4: top-level npm section keys are captured as npm:name@version."""
    versions = versions_deno_lock(_v4_lock(tmp_path))
    assert "npm:chalk@5.3.0" in versions


def test_v2_npm_captured(tmp_path: Path) -> None:
    """v2: top-level npm dict keys are captured as npm:name@version."""
    versions = versions_deno_lock(_v2_lock(tmp_path))
    assert "npm:chalk@5.1.0" in versions
    assert len(versions) == 1


def test_v3_and_v4_both_green_with_fixture_files() -> None:
    """Fixture files (tests/fixtures/) return expected counts."""
    v3_path = FIXTURE_DIR / "deno_v3.lock"
    v4_path = FIXTURE_DIR / "deno_v4.lock"
    if not v3_path.exists() or not v4_path.exists():
        pytest.skip("fixture files not found")
    v3 = versions_deno_lock(v3_path)
    v4 = versions_deno_lock(v4_path)
    assert len(v3) == 2, f"v3 fixture: expected 2, got {sorted(v3)}"
    assert len(v4) == 2, f"v4 fixture: expected 2, got {sorted(v4)}"


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
