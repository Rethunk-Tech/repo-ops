"""Python ecosystem wiring for package version-ceiling ("hold") rules.

Covers uv/hatch (repo_ops_py.uv_lock), pdm, pipenv, poetry, and pip-tools —
none of these managers has a per-package targeted install, so (except
pip-tools, which prevents the crossing via an ephemeral pip constraints
file passed to compile) each apply's clamp-down is the only enforcement
mechanism, not a safety net.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.holds import HoldRule
from repo_ops_models import Workspace


def _held_rule(package: str, **overrides) -> HoldRule:
    defaults = {"package": package, "max_version": "6"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _proc(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        ["cmd"], returncode, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# uv / hatch (repo_ops_py.uv_lock)
# ---------------------------------------------------------------------------


def _uv_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path, ecosystem="py", manager="uv", git_repo=tmp_path, label="demo"
    )


def test_check_uv_lock_filters_held_package_past_ceiling(tmp_path: Path) -> None:
    from repo_ops_py import uv_lock

    stdout = "Resolved 10 packages\nwould upgrade typescript-stub 6.5 -> 7.0\n"
    with (
        patch.object(
            uv_lock,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(uv_lock, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", return_value=_proc(stdout=stdout)),
    ):
        changes = uv_lock.check_uv_lock(_uv_ws(tmp_path))
    assert changes == []


def test_apply_uv_lock_clamps_package_above_ceiling(tmp_path: Path) -> None:
    from repo_ops_py import uv_lock

    lock_path = tmp_path / "uv.lock"
    versions = {"typescript-stub": "7.2.0"}

    def _write_lock():
        lock_path.write_text(
            "\n".join(
                f'[[package]]\nname = "{n}"\nversion = "{v}"\n'
                for n, v in versions.items()
            ),
            encoding="utf-8",
        )

    _write_lock()
    clamp_commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None, env=None):
        if cmd[:3] == ["uv", "lock", "--upgrade-package"]:
            clamp_commands.append(cmd)
            versions["typescript-stub"] = "6.9.0"
            _write_lock()
            return _proc()
        return _proc()

    with (
        patch.object(
            uv_lock,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(uv_lock, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", side_effect=fake_run),
    ):
        uv_lock.apply_uv_lock(_uv_ws(tmp_path))

    assert clamp_commands == [
        ["uv", "lock", "--upgrade-package", "typescript-stub<=6.999999.999999"]
    ]
    assert "typescript-stub@7" not in lock_path.read_text(encoding="utf-8")


def test_apply_uv_lock_raises_when_clamp_fails(tmp_path: Path) -> None:
    from repo_ops_py import uv_lock

    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )

    with (
        patch.object(
            uv_lock,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(uv_lock, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", return_value=_proc()),  # lock never changes
        pytest.raises(RuntimeError, match="hold"),
    ):
        uv_lock.apply_uv_lock(_uv_ws(tmp_path))


def test_apply_uv_lock_raises_when_corrective_install_command_fails(
    tmp_path: Path,
) -> None:
    """Distinct failure mode from 'landed but still over ceiling': the
    corrective `uv add` command itself errors out (e.g. resolver conflict)."""
    from repo_ops_py import uv_lock

    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )

    def fake_run(cmd, cwd, timeout=None, env=None):
        if cmd[:3] == ["uv", "lock", "--upgrade-package"]:
            return _proc(returncode=1, stderr="resolver conflict")
        return _proc()

    with (
        patch.object(
            uv_lock,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(uv_lock, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", side_effect=fake_run),
        pytest.raises(RuntimeError, match=r"hold.*resolver conflict"),
    ):
        uv_lock.apply_uv_lock(_uv_ws(tmp_path))


# ---------------------------------------------------------------------------
# pdm
# ---------------------------------------------------------------------------


def _pdm_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path, ecosystem="py", manager="pdm", git_repo=tmp_path, label="demo"
    )


def test_apply_pdm_clamps_package_above_ceiling(tmp_path: Path) -> None:
    from repo_ops_py_pdm import pdm

    lock_path = tmp_path / "pdm.lock"
    lock_path.write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )
    manifest_path = tmp_path / "pyproject.toml"
    manifest_before = b"[project]\nname = 'demo'\n"
    manifest_path.write_bytes(manifest_before)

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pdm", "add"]:
            manifest_path.write_bytes(b"[project]\nname = 'changed'\n")
            lock_path.write_text(
                '[[package]]\nname = "typescript-stub"\nversion = "6.9.0"\n',
                encoding="utf-8",
            )
        return _proc()

    with (
        patch.object(
            pdm, "_global_holds_cached", return_value=(_held_rule("typescript-stub"),)
        ),
        patch.object(pdm, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch.object(pdm, "run", side_effect=fake_run),
    ):
        pdm.apply_pdm(_pdm_ws(tmp_path))

    assert "6.9.0" in lock_path.read_text(encoding="utf-8")
    assert manifest_path.read_bytes() == manifest_before


def test_apply_pdm_raises_when_clamp_fails(tmp_path: Path) -> None:
    from repo_ops_py_pdm import pdm

    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )

    with (
        patch.object(
            pdm, "_global_holds_cached", return_value=(_held_rule("typescript-stub"),)
        ),
        patch.object(pdm, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_pdm.pdm.shutil.which", return_value="/usr/bin/pdm"),
        patch.object(pdm, "run", return_value=_proc()),
        pytest.raises(RuntimeError, match="hold"),
    ):
        pdm.apply_pdm(_pdm_ws(tmp_path))


# ---------------------------------------------------------------------------
# pipenv
# ---------------------------------------------------------------------------


def _pipenv_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pipenv",
        git_repo=tmp_path,
        label="demo",
    )


def test_apply_pipenv_clamps_package_above_ceiling(tmp_path: Path) -> None:
    from repo_ops_py_pipenv import pipenv

    lock_path = tmp_path / "Pipfile.lock"

    def _write_lock(version: str):
        lock_path.write_text(
            json.dumps({"default": {"typescript-stub": {"version": f"=={version}"}}}),
            encoding="utf-8",
        )

    _write_lock("7.2.0")
    manifest_path = tmp_path / "Pipfile"
    manifest_before = b"[packages]\ntypescript-stub = '*'\n"
    manifest_path.write_bytes(manifest_before)

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["pipenv", "install"]:
            manifest_path.write_bytes(b"[packages]\ntypescript-stub = 'changed'\n")
            _write_lock("6.9.0")
        return _proc()

    with (
        patch.object(
            pipenv,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(pipenv, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch.object(pipenv, "run", side_effect=fake_run),
    ):
        pipenv.apply_pipenv(_pipenv_ws(tmp_path))

    assert "6.9.0" in lock_path.read_text(encoding="utf-8")
    assert manifest_path.read_bytes() == manifest_before


def test_apply_pipenv_raises_when_clamp_fails(tmp_path: Path) -> None:
    from repo_ops_py_pipenv import pipenv

    (tmp_path / "Pipfile.lock").write_text(
        json.dumps({"default": {"typescript-stub": {"version": "==7.2.0"}}}),
        encoding="utf-8",
    )

    with (
        patch.object(
            pipenv,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(pipenv, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch.object(pipenv, "run", return_value=_proc()),
        pytest.raises(RuntimeError, match="hold"),
    ):
        pipenv.apply_pipenv(_pipenv_ws(tmp_path))


# ---------------------------------------------------------------------------
# poetry
# ---------------------------------------------------------------------------


def _poetry_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="poetry",
        git_repo=tmp_path,
        label="demo",
    )


def test_apply_poetry_clamps_package_above_ceiling(tmp_path: Path) -> None:
    from repo_ops_py_poetry import poetry

    lock_path = tmp_path / "poetry.lock"
    lock_path.write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )
    manifest_path = tmp_path / "pyproject.toml"
    manifest_before = b"[project]\nname = 'demo'\n"
    manifest_path.write_bytes(manifest_before)

    def fake_run(cmd, cwd, timeout=None):
        if cmd[:2] == ["poetry", "add"]:
            manifest_path.write_bytes(b"[project]\nname = 'changed'\n")
            lock_path.write_text(
                '[[package]]\nname = "typescript-stub"\nversion = "6.9.0"\n',
                encoding="utf-8",
            )
        return _proc()

    with (
        patch.object(
            poetry,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(poetry, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch.object(poetry, "run", side_effect=fake_run),
    ):
        poetry.apply_poetry(_poetry_ws(tmp_path))

    assert "6.9.0" in lock_path.read_text(encoding="utf-8")
    assert manifest_path.read_bytes() == manifest_before


def test_apply_poetry_raises_when_clamp_fails(tmp_path: Path) -> None:
    from repo_ops_py_poetry import poetry

    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )

    with (
        patch.object(
            poetry,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(poetry, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_poetry.poetry.shutil.which", return_value="/usr/bin/poetry"),
        patch.object(poetry, "run", return_value=_proc()),
        pytest.raises(RuntimeError, match="hold"),
    ):
        poetry.apply_poetry(_poetry_ws(tmp_path))


# ---------------------------------------------------------------------------
# pip-tools (constraints-file prevention, not a post-hoc clamp)
# ---------------------------------------------------------------------------


def _pip_tools_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="pip-tools",
        git_repo=tmp_path,
        label="demo",
    )


def test_apply_pip_tools_passes_hold_constraints_file(tmp_path: Path) -> None:
    from repo_ops_py_pip_tools import pip_tools

    (tmp_path / "requirements.in").write_text(
        "typescript-stub\nother-pkg\n", encoding="utf-8"
    )
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured_cmds.append(cmd)
        return _proc(returncode=0)

    with (
        patch.object(
            pip_tools,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(pip_tools, "_repo_holds_cached", return_value=()),
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"
        ),
        patch.object(pip_tools, "run", side_effect=fake_run),
    ):
        pip_tools.apply_pip_tools(_pip_tools_ws(tmp_path))

    assert captured_cmds, "expected at least one compile invocation"
    compile_cmd = captured_cmds[0]
    assert "-c" in compile_cmd
    constraints_path = Path(compile_cmd[compile_cmd.index("-c") + 1])
    # The constraints file must be cleaned up after apply returns.
    assert not constraints_path.exists()


def test_pip_tools_constraints_file_contains_only_held_direct_deps(
    tmp_path: Path,
) -> None:
    from repo_ops_py_pip_tools import pip_tools

    (tmp_path / "requirements.in").write_text(
        "typescript-stub\nother-pkg\n", encoding="utf-8"
    )
    ws = _pip_tools_ws(tmp_path)
    with (
        patch.object(
            pip_tools,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(pip_tools, "_repo_holds_cached", return_value=()),
    ):
        with pip_tools._hold_constraints_file(
            ws, tmp_path / "requirements.in"
        ) as constraints_path:
            assert constraints_path is not None
            content = constraints_path.read_text(encoding="utf-8")
            assert "typescript-stub<=6.999999.999999" in content
            assert "other-pkg" not in content
        assert not constraints_path.exists()
