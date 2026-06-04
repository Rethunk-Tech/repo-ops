"""Regression tests for scripts/set_version.py (release version tool)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
