"""Regression tests for scripts/check_import_boundaries.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_import_boundaries.py"
_spec = importlib.util.spec_from_file_location("check_import_boundaries", _SCRIPT)
assert _spec is not None and _spec.loader is not None
check_import_boundaries = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_import_boundaries)


def test_dotless_import_from_repo_ops_upgrade_disallowed_and_allowed(
    tmp_path: Path,
) -> None:
    """Dotless `from repo_ops_upgrade import X` must be subject to the allowlist.

    ``orchestration`` is not in ALLOWED_UPGRADE_SUBMODULES → upgrade_subs should
    contain it so the disallowed-submodule check fires.
    ``registry`` IS in ALLOWED_UPGRADE_SUBMODULES → upgrade_subs - allowed == empty.
    """
    bad = tmp_path / "bad.py"
    bad.write_text("from repo_ops_upgrade import orchestration\n", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("from repo_ops_upgrade import registry\n", encoding="utf-8")

    _, bad_subs = check_import_boundaries._imports_in_file(bad)
    _, good_subs = check_import_boundaries._imports_in_file(good)

    allowed = check_import_boundaries.ALLOWED_UPGRADE_SUBMODULES
    assert "orchestration" in bad_subs
    assert bad_subs - allowed == {"orchestration"}
    assert "registry" in good_subs
    assert good_subs - allowed == set()


def test_lockfiles_workflow_uses_allowed_orchestration_rejected(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed.py"
    allowed.write_text(
        "from repo_ops_upgrade.workflow_uses import parse_workflow_uses\n",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.py"
    bad.write_text("from repo_ops_upgrade import orchestration\n", encoding="utf-8")

    _, allowed_subs = check_import_boundaries._imports_in_file(allowed)
    _, bad_subs = check_import_boundaries._imports_in_file(bad)
    narrow = check_import_boundaries.ALLOWED_UPGRADE_SUBMODULES_BY_PKG[
        "repo_ops_lockfiles"
    ]

    assert allowed_subs <= narrow
    assert bad_subs - narrow == {"orchestration"}


def test_pm_cross_import_rejected(tmp_path: Path) -> None:
    py = tmp_path / "npm.py"
    py.write_text("import repo_ops_js_yarn\n", encoding="utf-8")
    imports, _ = check_import_boundaries._imports_in_file(py)
    errors: list[str] = []
    mod_root = "repo_ops_js_npm"
    for other in check_import_boundaries.PM_PREFIXES:
        if other != mod_root and other in imports:
            errors.append(f"{py}: PM package must not import {other}")
    assert errors == [f"{py}: PM package must not import repo_ops_js_yarn"]


def test_lockfiles_forbidden_pm_import(tmp_path: Path) -> None:
    py = tmp_path / "sbom.py"
    py.write_text("import repo_ops_js\n", encoding="utf-8")
    imports, _ = check_import_boundaries._imports_in_file(py)
    forbidden = check_import_boundaries.FORBIDDEN["repo_ops_lockfiles"]
    assert "repo_ops_js" in imports & forbidden


def test_repo_ops_container_in_pm_prefixes() -> None:
    assert "repo_ops_container" in check_import_boundaries.PM_PREFIXES


def test_container_pm_upgrade_submodule_allowlist(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("from repo_ops_upgrade import registry\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("from repo_ops_upgrade import orchestration\n", encoding="utf-8")

    _, good_subs = check_import_boundaries._imports_in_file(good)
    _, bad_subs = check_import_boundaries._imports_in_file(bad)
    allowed = check_import_boundaries.ALLOWED_UPGRADE_SUBMODULES

    assert good_subs - allowed == set()
    assert bad_subs - allowed == {"orchestration"}


def test_remediations_js_allowed_and_cross_import_rejected(
    tmp_path: Path,
) -> None:
    good = tmp_path / "good.py"
    good.write_text(
        "from repo_ops_js.workspaces import parse_package_manager\n"
        "from repo_ops_upgrade.constants import BIOME_PKG\n",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.py"
    bad.write_text("import repo_ops_upgrade_remediations_go\n", encoding="utf-8")

    good_errors: list[str] = []
    good_imports, good_subs = check_import_boundaries._imports_in_file(good)
    check_import_boundaries._check_remediations(
        "repo_ops_upgrade_remediations_js",
        good_imports,
        good_subs,
        good,
        good_errors,
    )
    assert good_errors == []

    bad_errors: list[str] = []
    bad_imports, bad_subs = check_import_boundaries._imports_in_file(bad)
    check_import_boundaries._check_remediations(
        "repo_ops_upgrade_remediations_js",
        bad_imports,
        bad_subs,
        bad,
        bad_errors,
    )
    assert bad_errors == [
        f"{bad}: remediations package must not import repo_ops_upgrade_remediations_go"
    ]


def test_audit_orchestration_import_allowed(tmp_path: Path) -> None:
    py = tmp_path / "audit.py"
    py.write_text("from repo_ops_upgrade import orchestration\n", encoding="utf-8")
    imports, upgrade_subs = check_import_boundaries._imports_in_file(py)
    errors: list[str] = []
    check_import_boundaries._check_app_pkg(
        "repo_ops_audit", imports, upgrade_subs, py, errors
    )
    assert errors == []


def test_main_returns_nonzero_on_synthetic_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "repo-ops-lockfiles" / "src" / "repo_ops_lockfiles"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text("import repo_ops_js\n", encoding="utf-8")
    monkeypatch.setattr(check_import_boundaries, "PACKAGES", tmp_path)
    assert check_import_boundaries.main() == 1


def test_main_returns_zero_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "repo-ops-doctor" / "src" / "repo_ops_doctor"
    pkg.mkdir(parents=True)
    (pkg / "doctor.py").write_text(
        "from repo_ops_core.cli import add_json_argument\n", encoding="utf-8"
    )
    monkeypatch.setattr(check_import_boundaries, "PACKAGES", tmp_path)
    assert check_import_boundaries.main() == 0
