"""Every applier refuses a manifest symlinked out of the repo.

The class of bug these cover is one writer at a time escaping the repository:
an outside file got rewritten, and the snapshot step skipped the link so no
rollback could put it back. Each applier takes the same precondition, so this
enumerates them rather than testing one at a time.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from repo_ops_container import container
from repo_ops_go import go
from repo_ops_js import deno, workspaces
from repo_ops_js_bun import bun
from repo_ops_js_npm import npm
from repo_ops_js_pnpm import pnpm
from repo_ops_js_yarn import yarn
from repo_ops_models import Change, Workspace
from repo_ops_nuget import nuget
from repo_ops_py import uv_lock
from repo_ops_py_hatch import hatch
from repo_ops_py_pdm import pdm
from repo_ops_py_pip_tools import pip_tools
from repo_ops_py_pipenv import pipenv
from repo_ops_py_poetry import poetry
from repo_ops_rust import rust
from repo_ops_upgrade import precommit, soup, toolchains

_MANIFEST = "pyproject.toml"
_BODY = '[project]\nname = "demo"\n'


def _workspace(root: Path, manager: str = "npm") -> Workspace:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    ecosystem = {
        "go": "go",
        "rust": "rust",
        "nuget": "nuget",
        "container": "container",
        "deno": "deno",
        "precommit": "precommit",
    }.get(manager, "python")
    return Workspace(path=root, git_repo=root, ecosystem=ecosystem, manager=manager, label="repo")


def _escaping_manifest(root: Path, outside: Path, name: str = _MANIFEST) -> Path:
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(_BODY, encoding="utf-8")
    (root / name).symlink_to(outside)
    return root / name


def _no_subprocess(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
    return CompletedProcess([], 0, "", "")


def _apply_go(ws: Workspace) -> None:
    with (
        patch.object(go, "run", _no_subprocess),
        patch.object(go, "_plan_go_work_directive_changes", return_value=[]),
    ):
        go.apply_go(ws)


def _apply_rust(ws: Workspace) -> None:
    with patch.object(rust, "run", _no_subprocess):
        rust.apply_rust(ws)


def _apply_nuget(ws: Workspace) -> None:
    with (
        patch.object(nuget, "run", _no_subprocess),
        patch.object(
            nuget, "_candidates", return_value=([Change("Serilog", "1.0.0", "2.0.0")], [])
        ),
    ):
        nuget.apply_nuget(ws)


def _apply_container(ws: Workspace) -> None:
    with patch.object(container, "check_container", return_value=[]):
        container.apply_container(ws)


def _apply_deno(ws: Workspace) -> None:
    with (
        patch.object(deno.shutil, "which", return_value="/usr/bin/deno"),
        patch.object(deno, "run", _no_subprocess),
    ):
        deno.apply_deno(ws)


def _apply_pip_tools(ws: Workspace) -> None:
    with patch.object(pip_tools, "_compile_requirements") as compile_mock:
        pip_tools.apply_pip_tools(ws)
    compile_mock.assert_not_called()


def _apply_hatch(ws: Workspace) -> None:
    with (
        patch.object(hatch.shutil, "which", return_value="/usr/bin/uv"),
        patch.object(hatch, "run", _no_subprocess),
        patch.object(hatch, "apply_uv_lock") as uv_mock,
    ):
        hatch.apply_hatch(ws)
    uv_mock.assert_not_called()


def _apply_poetry(ws: Workspace) -> None:
    with (
        patch.object(poetry.shutil, "which", return_value="/usr/bin/poetry"),
        patch.object(poetry, "run", _no_subprocess),
    ):
        poetry.apply_poetry(ws)


def _apply_pdm(ws: Workspace) -> None:
    with (
        patch.object(pdm.shutil, "which", return_value="/usr/bin/pdm"),
        patch.object(pdm, "run", _no_subprocess),
    ):
        pdm.apply_pdm(ws)


def _apply_pipenv(ws: Workspace) -> None:
    with (
        patch.object(pipenv.shutil, "which", return_value="/usr/bin/pipenv"),
        patch.object(pipenv, "run", _no_subprocess),
    ):
        pipenv.apply_pipenv(ws)


def _apply_uv(ws: Workspace) -> None:
    with patch.object(uv_lock, "run", _no_subprocess):
        uv_lock.apply_uv_lock(ws)


def _apply_npm(ws: Workspace) -> None:
    with (
        patch.object(npm, "run", _no_subprocess),
        patch.object(npm, "_npm_outdated", return_value=([], [])),
    ):
        npm.apply_npm(ws)


def _apply_bun(ws: Workspace) -> None:
    with (
        patch.object(bun, "run", _no_subprocess),
        patch.object(bun, "check_bun", return_value=([], [])),
    ):
        bun.apply_bun(ws)


def _apply_pnpm(ws: Workspace) -> None:
    with (
        patch.object(pnpm, "run", _no_subprocess),
        patch.object(pnpm, "_pnpm_outdated", return_value=([], [])),
    ):
        pnpm.apply_pnpm(ws)


def _apply_yarn(ws: Workspace) -> None:
    with (
        patch.object(yarn, "run", _no_subprocess),
        patch.object(yarn, "_plan_without_resolutions", return_value=[]),
    ):
        yarn.apply_yarn(ws)


def _apply_precommit(ws: Workspace) -> None:
    with patch.object(precommit, "check_precommit", return_value=[]):
        precommit.apply_precommit(ws)


def _apply_soup(ws: Workspace) -> None:
    soup.sync_soup_versions(ws.git_repo, [Change("flask", "2.0", "3.0")])


def _apply_toolchains(ws: Workspace) -> None:
    toolchains.apply_toolchains(ws)


_APPLIERS: dict[str, tuple[str, Callable[[Workspace], object]]] = {
    "go": ("go.mod", _apply_go),
    "rust": ("Cargo.toml", _apply_rust),
    "nuget": ("app.csproj", _apply_nuget),
    "container": ("Dockerfile", _apply_container),
    "deno": ("deno.json", _apply_deno),
    "pip-tools": ("requirements.in", _apply_pip_tools),
    "hatch": ("pyproject.toml", _apply_hatch),
    "poetry": ("pyproject.toml", _apply_poetry),
    "pdm": ("pyproject.toml", _apply_pdm),
    "pipenv": ("Pipfile", _apply_pipenv),
    "uv": ("pyproject.toml", _apply_uv),
    "npm": ("package.json", _apply_npm),
    "bun": ("package.json", _apply_bun),
    "pnpm": ("package.json", _apply_pnpm),
    "yarn": ("package.json", _apply_yarn),
    "precommit": (".pre-commit-config.yaml", _apply_precommit),
    "soup": ("SOUP.md", _apply_soup),
    "toolchains": ("go.mod", _apply_toolchains),
}


@pytest.mark.parametrize(("manager", "manifest"), [(m, f[0]) for m, f in _APPLIERS.items()])
def test_applier_leaves_symlinked_manifest_outside_the_repo_alone(
    tmp_path: Path, manager: str, manifest: str
) -> None:
    ws = _workspace(tmp_path / "repo", manager)
    outside = tmp_path / "outside" / manifest
    link = _escaping_manifest(ws.path, outside, manifest)
    if manager == "soup":
        (ws.path / ".repo-ops.toml").write_text('[soup]\npath = "SOUP.md"\n', encoding="utf-8")
        outside.write_text("| flask | 2.0 |\n", encoding="utf-8")

    _APPLIERS[manager][1](ws)

    assert outside.read_text(encoding="utf-8") in (_BODY, "| flask | 2.0 |\n")
    assert link.is_symlink()


def test_deno_import_map_symlinked_outside_the_repo_is_not_rewritten(tmp_path: Path) -> None:
    ws = _workspace(tmp_path / "repo", "deno")
    outside = tmp_path / "outside" / "deno.json"
    outside.parent.mkdir()
    body = '{\n  "imports": {\n    "npm:is-odd": "npm:is-odd@0.1.2"\n  }\n}\n'
    outside.write_text(body, encoding="utf-8")
    (ws.path / "deno.json").symlink_to(outside)

    assert deno._release_unheld_deno_pin(ws, "npm:is-odd") is False
    assert outside.read_text(encoding="utf-8") == body
    assert (ws.path / "deno.json").is_symlink()


def test_deno_apply_skips_a_symlinked_config(tmp_path: Path) -> None:
    ws = _workspace(tmp_path / "repo", "deno")
    outside = tmp_path / "outside" / "deno.json"
    outside.parent.mkdir()
    outside.write_text(_BODY, encoding="utf-8")
    (ws.path / "deno.json").symlink_to(outside)

    with (
        patch.object(deno.shutil, "which", return_value="/usr/bin/deno"),
        patch.object(deno, "run") as run_mock,
    ):
        assert deno.apply_deno(ws) == []

    run_mock.assert_not_called()
    assert outside.read_text(encoding="utf-8") == _BODY


def test_pip_tools_hold_constraints_symlinked_outside_the_repo_is_not_written(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path / "repo", "pip-tools")
    (ws.path / "requirements.in").write_text("flask>=2.0\n", encoding="utf-8")
    (ws.path / ".repo-ops.toml").write_text(
        '[[hold]]\npackage = "flask"\nmax_version = "2.9"\n', encoding="utf-8"
    )
    outside = tmp_path / "outside" / ".repo-ops-holds.txt"
    outside.parent.mkdir()
    body = "keep me\n"
    outside.write_text(body, encoding="utf-8")
    (ws.path / ".repo-ops-holds.txt").symlink_to(outside)

    with pip_tools._hold_constraints_file(ws, ws.path / "requirements.in") as constraints:
        assert constraints is None

    assert outside.read_text(encoding="utf-8") == body
    assert (ws.path / ".repo-ops-holds.txt").is_symlink()


def test_pip_tools_check_writes_nothing_outside_the_repo(tmp_path: Path) -> None:
    ws = _workspace(tmp_path / "repo", "pip-tools")
    (ws.path / "requirements.in").write_text("flask>=2.0\n", encoding="utf-8")
    (ws.path / ".repo-ops.toml").write_text(
        '[[hold]]\npackage = "flask"\nmax_version = "2.9"\n', encoding="utf-8"
    )
    outside = tmp_path / "outside" / ".repo-ops-holds.txt"
    outside.parent.mkdir()
    body = "keep me\n"
    outside.write_text(body, encoding="utf-8")
    (ws.path / ".repo-ops-holds.txt").symlink_to(outside)

    with patch.object(pip_tools.shutil, "which", return_value=None):
        pip_tools.check_pip_tools(ws)

    assert outside.read_text(encoding="utf-8") == body
    assert (ws.path / ".repo-ops-holds.txt").is_symlink()


def test_npm_apply_leaves_a_lockfile_symlinked_outside_the_repo_alone(tmp_path: Path) -> None:
    ws = _workspace(tmp_path / "repo", "npm")
    (ws.path / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
    outside = tmp_path / "outside" / "package-lock.json"
    outside.parent.mkdir()
    body = '{"name": "demo"}\n'
    outside.write_text(body, encoding="utf-8")
    (ws.path / "package-lock.json").symlink_to(outside)

    with patch.object(npm, "run") as run_mock:
        assert npm.apply_npm(ws) == []

    run_mock.assert_not_called()
    assert outside.read_text(encoding="utf-8") == body


def test_applier_still_applies_through_an_in_repo_link(tmp_path: Path) -> None:
    """A link resolving inside the repo is ours: the bump lands and the link stays."""
    ws = _workspace(tmp_path / "repo", "poetry")
    target = ws.path / "pyproject.base.toml"
    target.write_text('[tool.poetry.dependencies]\nflask = "^2.0"\n', encoding="utf-8")
    (ws.path / "pyproject.toml").symlink_to(target)

    with (
        patch.object(poetry.shutil, "which", return_value="/usr/bin/poetry"),
        patch.object(poetry, "run", _no_subprocess),
        patch.object(poetry, "_enforce_version_ceilings"),
        patch.object(poetry, "_apply_poetry_targeted_bumps", return_value=[]),
    ):
        assert poetry.apply_poetry(ws) == []

    assert (ws.path / "pyproject.toml").is_symlink()


def test_guarded_write_refuses_an_outside_target(tmp_path: Path) -> None:
    from repo_ops_core.filesystem import OutsideRepoError, guarded_write

    root = tmp_path / "repo"
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    link = root / "linked.txt"
    root.mkdir()
    os.symlink(outside, link)

    with pytest.raises(OutsideRepoError):
        guarded_write(link, b"new", root)

    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_lockfile_for_names_the_manager_lockfile(tmp_path: Path) -> None:
    assert workspaces.lockfile_for(tmp_path, "npm").name == "package-lock.json"
    assert workspaces.lockfile_for(tmp_path, "pnpm").name == "pnpm-lock.yaml"
    assert workspaces.lockfile_for(tmp_path, "yarn").name == "yarn.lock"
