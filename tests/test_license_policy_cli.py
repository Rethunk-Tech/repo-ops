"""CLI-entrypoint coverage for the repo-ops license subcommand.

Targets ``license_policy.main()`` end to end (argument parsing, scan
dispatch, both output modes, exit-code selection) — the entrypoint itself
had no direct coverage previously (unit tests exercised scan/policy helpers
individually, and the human-output happy/violation paths, but never the
JSON *and* human code paths together against a single realistic fixture
tree with both an allow-list violation and an unknown license present).

Uses in-memory tmp_path fixtures; no network or subprocess calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from repo_ops_lockfiles.license_policy import main


def _write_nm_pkg(tmp_path: Path, name: str, version: str, lic: str | None) -> None:
    pkg_dir = tmp_path / "node_modules" / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"name": name, "version": version}
    if lic is not None:
        payload["license"] = lic
    (pkg_dir / "package.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def mixed_tree(tmp_path: Path) -> Path:
    """One denied-license package, one license-less (UNKNOWN) package."""
    _write_nm_pkg(tmp_path, "copyleft-lib", "1.0.0", "GPL-3.0")
    _write_nm_pkg(tmp_path, "mystery-lib", "2.0.0", None)
    return tmp_path


def test_main_json_compact_violation_and_unknown(
    mixed_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--json --compact against a deny-list violation → exit 1 (violations win)."""
    rc = main(["--json", "--compact", "--deny", "GPL-3.0", str(mixed_tree)])
    assert rc == 1

    out = capsys.readouterr().out
    assert "\n" not in out.strip()  # single-line JSON (--compact)
    payload = json.loads(out)
    assert payload["schema_version"] == 1
    assert payload["summary"]["violations"] == 1
    assert payload["summary"]["unknown"] == 1
    assert any(v["name"] == "copyleft-lib" for v in payload["violations"])
    assert any(u["name"] == "mystery-lib" for u in payload["unknown"])


def test_main_human_output_unknown_only_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Human output, no policy list, only an unknown license → exit 2."""
    _write_nm_pkg(tmp_path, "mystery-lib", "2.0.0", None)
    rc = main([str(tmp_path)])
    assert rc == 2

    out = capsys.readouterr().out
    assert "License scan:" in out
    assert "Unknown licenses" in out
    assert "mystery-lib" in out
    assert "Note: license data is best-effort" in out
    # Violations section is only printed when violations exist.
    assert "Violations:" not in out


def test_main_human_output_violation_exit_1(
    mixed_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Human output with an allow-list violation prints the Violations block."""
    rc = main(["--allow", "MIT", "--no-fail-unknown", str(mixed_tree)])
    assert rc == 1

    out = capsys.readouterr().out
    assert "Violations:" in out
    assert "copyleft-lib@1.0.0" in out
    assert "[GPL-3.0]" in out
