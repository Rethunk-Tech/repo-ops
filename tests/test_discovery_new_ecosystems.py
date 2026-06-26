"""Discovery tests for PDM, pipenv, and Deno ecosystems."""

from __future__ import annotations

import subprocess
from pathlib import Path

from repo_ops_upgrade.discovery import DiscoveryDiagnostics, discover_all


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


# ---------------------------------------------------------------------------
# Task A: Pipfile without Pipfile.lock → accurate skip reason
# ---------------------------------------------------------------------------


def test_pipfile_only_produces_accurate_skip(tmp_path: Path) -> None:
    """Regression for #15: a Pipfile-only repo must NOT emit the misleading
    'pyproject.toml without a recognized manager' skip reason."""
    repo = tmp_path / "org" / "pipfile-only"
    _git_init(repo)
    (repo / "Pipfile").write_text(
        '[packages]\nrequests = "*"\n'
    )

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], flat=False, diagnostics=diags)

    py_found = [ws for ws in found if ws.ecosystem == "py"]
    assert py_found == [], f"Pipfile-only must not produce a workspace; got {py_found}"

    py_skips = [s for s in diags.skipped if s["ecosystem"] == "py"]
    assert py_skips, "Pipfile-only repo must be recorded as a skip"
    reason = py_skips[0]["reason"]
    assert "pyproject.toml" not in reason, (
        f"misleading skip reason still present: {reason!r}"
    )
    assert "pipenv" in reason.lower() or "Pipfile" in reason, (
        f"expected Pipfile/pipenv in skip reason; got {reason!r}"
    )


# ---------------------------------------------------------------------------
# PDM
# ---------------------------------------------------------------------------


def test_discover_pdm_with_pdm_lock(tmp_path: Path) -> None:
    """A repo with pyproject.toml + pdm.lock is discovered as manager=pdm."""
    repo = tmp_path / "org" / "pdm-project"
    _git_init(repo)
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'mylib'\n\n[tool.pdm]\n"
    )
    (repo / "pdm.lock").write_text(
        "[metadata]\nlock_version = '4.4.1'\n\n[[package]]\nname = 'requests'\nversion = '2.31.0'\n"
    )

    found = discover_all([str(tmp_path)], flat=False)
    pdm_found = [ws for ws in found if ws.manager == "pdm"]

    assert len(pdm_found) == 1, f"expected one pdm workspace; found={pdm_found}"
    assert pdm_found[0].ecosystem == "py"
    assert pdm_found[0].path == repo.resolve()


def test_discover_pdm_lock_without_pyproject_triggers_py(tmp_path: Path) -> None:
    """pdm.lock alone (no pyproject.toml) also triggers consider_py and yields pdm."""
    repo = tmp_path / "org" / "pdm-lock-only"
    _git_init(repo)
    # pdm.lock without pyproject.toml (unusual but should work)
    (repo / "pdm.lock").write_text(
        "[metadata]\nlock_version = '4.4.1'\n\n[[package]]\nname = 'click'\nversion = '8.1.0'\n"
    )

    found = discover_all([str(tmp_path)], flat=False)
    pdm_found = [ws for ws in found if ws.manager == "pdm"]

    assert len(pdm_found) == 1, f"expected one pdm workspace; found={pdm_found}"


def test_discover_pdm_tool_section_without_lock_skips_accurately(tmp_path: Path) -> None:
    """pyproject.toml with [tool.pdm] but no pdm.lock → skip with accurate message."""
    repo = tmp_path / "org" / "pdm-no-lock"
    _git_init(repo)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n\n[tool.pdm]\n")

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], flat=False, diagnostics=diags)

    py_found = [ws for ws in found if ws.ecosystem == "py"]
    assert py_found == [], f"must not produce a workspace without pdm.lock; got {py_found}"

    py_skips = [s for s in diags.skipped if s["ecosystem"] == "py"]
    assert py_skips, "must record a skip"
    assert "pdm" in py_skips[0]["reason"].lower(), (
        f"expected pdm in skip reason; got {py_skips[0]['reason']!r}"
    )


# ---------------------------------------------------------------------------
# pipenv
# ---------------------------------------------------------------------------


def test_discover_pipenv_with_lock(tmp_path: Path) -> None:
    """A repo with Pipfile + Pipfile.lock is discovered as manager=pipenv."""
    repo = tmp_path / "org" / "pipenv-project"
    _git_init(repo)
    (repo / "Pipfile").write_text('[packages]\nrequests = "*"\n')
    (repo / "Pipfile.lock").write_text(
        '{"_meta":{},"default":{"requests":{"version":"==2.31.0"}},"develop":{}}'
    )

    found = discover_all([str(tmp_path)], flat=False)
    pipenv_found = [ws for ws in found if ws.manager == "pipenv"]

    assert len(pipenv_found) == 1, f"expected one pipenv workspace; found={pipenv_found}"
    assert pipenv_found[0].ecosystem == "py"
    assert pipenv_found[0].path == repo.resolve()


def test_discover_pipenv_no_lock_skips_with_pipenv_reason(tmp_path: Path) -> None:
    """Pipfile without Pipfile.lock → skip with Pipfile/pipenv reason (not pyproject msg)."""
    repo = tmp_path / "org" / "pipenv-no-lock"
    _git_init(repo)
    (repo / "Pipfile").write_text('[packages]\nrequests = "*"\n')

    diags = DiscoveryDiagnostics()
    found = discover_all([str(tmp_path)], flat=False, diagnostics=diags)

    py_found = [ws for ws in found if ws.ecosystem == "py"]
    assert py_found == [], "no workspace without Pipfile.lock"

    py_skips = [s for s in diags.skipped if s["ecosystem"] == "py"]
    assert py_skips, "must record a skip"
    reason = py_skips[0]["reason"]
    assert "pyproject.toml" not in reason, (
        f"misleading pyproject skip message still present: {reason!r}"
    )
    assert "pipenv" in reason.lower() or "Pipfile" in reason, (
        f"expected Pipfile/pipenv in skip reason; got {reason!r}"
    )


# ---------------------------------------------------------------------------
# Deno
# ---------------------------------------------------------------------------


def test_discover_deno_with_deno_json(tmp_path: Path) -> None:
    """A repo with deno.json is discovered as ecosystem=deno, manager=deno."""
    repo = tmp_path / "org" / "deno-project"
    _git_init(repo)
    (repo / "deno.json").write_text('{"name":"my-deno-app","version":"0.1.0"}\n')
    (repo / "deno.lock").write_text('{"version":"3","packages":{}}\n')

    found = discover_all([str(tmp_path)], flat=False)
    deno_found = [ws for ws in found if ws.ecosystem == "deno"]

    assert len(deno_found) == 1, f"expected one deno workspace; found={deno_found}"
    assert deno_found[0].manager == "deno"
    assert deno_found[0].path == repo.resolve()


def test_discover_deno_with_deno_jsonc(tmp_path: Path) -> None:
    """deno.jsonc also triggers Deno discovery."""
    repo = tmp_path / "org" / "deno-jsonc"
    _git_init(repo)
    (repo / "deno.jsonc").write_text('// deno config\n{"name":"app"}\n')

    found = discover_all([str(tmp_path)], flat=False)
    deno_found = [ws for ws in found if ws.ecosystem == "deno"]

    assert len(deno_found) == 1, f"expected one deno workspace; found={deno_found}"


def test_discover_deno_with_lock_only(tmp_path: Path) -> None:
    """deno.lock alone (no deno.json) still triggers Deno discovery."""
    repo = tmp_path / "org" / "deno-lock-only"
    _git_init(repo)
    (repo / "deno.lock").write_text('{"version":"3","packages":{}}\n')

    found = discover_all([str(tmp_path)], flat=False)
    deno_found = [ws for ws in found if ws.ecosystem == "deno"]

    assert len(deno_found) == 1


def test_discover_deno_not_inside_git_is_skipped(tmp_path: Path) -> None:
    """A deno.json outside any git repo is recorded as a skip (not a workspace)."""
    nogt = tmp_path / "nogt"
    nogt.mkdir()
    (nogt / "deno.json").write_text('{"name":"app"}\n')

    diags = DiscoveryDiagnostics()
    discover_all([str(tmp_path)], flat=True, diagnostics=diags)

    deno_skips = [s for s in diags.skipped if s["ecosystem"] == "deno"]
    assert deno_skips, "deno manifest outside git must be recorded as skip"
    assert "git" in deno_skips[0]["reason"].lower()
