"""Python ecosystem wiring for package version-ceiling ("hold") rules.

Covers uv/hatch (repo_ops_py.uv_lock), pdm, pipenv, poetry, and pip-tools —
none of these managers has a per-package targeted install, so (except
pip-tools, which prevents the crossing via an ephemeral pip constraints
file passed to compile) each apply's clamp-down is the only enforcement
mechanism, not a safety net.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core import holds as core_holds
from repo_ops_core.holds import HoldRule
from repo_ops_models import Workspace


def _held_rule(package: str, **overrides) -> HoldRule:
    defaults = {"package": package, "max_version": "6"}
    defaults.update(overrides)
    return HoldRule(**defaults)


# ---------------------------------------------------------------------------
# uv / hatch (repo_ops_py.uv_lock)
# ---------------------------------------------------------------------------


def _uv_ws(tmp_path: Path) -> Workspace:
    return Workspace(path=tmp_path, ecosystem="py", manager="uv", git_repo=tmp_path, label="demo")


def test_check_uv_lock_filters_held_package_past_ceiling(tmp_path: Path, make_proc) -> None:
    from repo_ops_py import uv_lock

    stdout = "Resolved 10 packages\nwould upgrade typescript-stub 6.5 -> 7.0\n"
    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", return_value=make_proc(stdout=stdout)),
    ):
        changes = uv_lock.check_uv_lock(_uv_ws(tmp_path))
    assert changes == []


def test_apply_uv_lock_clamps_package_above_ceiling(tmp_path: Path, make_proc) -> None:
    from repo_ops_py import uv_lock

    lock_path = tmp_path / "uv.lock"
    versions = {"typescript-stub": "7.2.0"}

    def _write_lock():
        lock_path.write_text(
            "\n".join(f'[[package]]\nname = "{n}"\nversion = "{v}"\n' for n, v in versions.items()),
            encoding="utf-8",
        )

    _write_lock()
    clamp_commands: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None, env=None):
        if cmd[:3] == ["uv", "lock", "--upgrade-package"]:
            clamp_commands.append(cmd)
            versions["typescript-stub"] = "6.9.0"
            _write_lock()
            return make_proc()
        return make_proc()

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", side_effect=fake_run),
    ):
        uv_lock.apply_uv_lock(_uv_ws(tmp_path))

    assert clamp_commands == [
        ["uv", "lock", "--upgrade-package", "typescript-stub<=6.999999.999999"]
    ]
    assert "typescript-stub@7" not in lock_path.read_text(encoding="utf-8")


def test_apply_uv_lock_raises_when_clamp_fails(tmp_path: Path, make_proc) -> None:
    from repo_ops_py import uv_lock

    lock_path = tmp_path / "uv.lock"
    lock_path.write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", return_value=make_proc()),  # lock never changes
        pytest.raises(RuntimeError, match="hold"),
    ):
        uv_lock.apply_uv_lock(_uv_ws(tmp_path))


def test_apply_uv_lock_raises_when_corrective_install_command_fails(
    tmp_path: Path,
    make_proc,
) -> None:
    """Distinct failure mode from 'landed but still over ceiling': the
    corrective lock command itself errors out (e.g. resolver conflict)."""
    from repo_ops_py import uv_lock

    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )

    def fake_run(cmd, cwd, timeout=None, env=None):
        if cmd[:3] == ["uv", "lock", "--upgrade-package"]:
            return make_proc(returncode=1, stderr="resolver conflict")
        return make_proc()

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch.object(uv_lock, "run", side_effect=fake_run),
        pytest.raises(RuntimeError, match=r"hold.*resolver conflict"),
    ):
        uv_lock.apply_uv_lock(_uv_ws(tmp_path))


# ---------------------------------------------------------------------------
# pdm
# ---------------------------------------------------------------------------


def _pdm_ws(tmp_path: Path) -> Workspace:
    return Workspace(path=tmp_path, ecosystem="py", manager="pdm", git_repo=tmp_path, label="demo")


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


def test_apply_pipenv_clamps_package_above_ceiling(tmp_path: Path, make_proc) -> None:
    """pipenv's Pipfile.lock is JSON, unlike pdm/poetry's TOML — kept separate from
    the shared clamp-down parametrization below."""
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
        return make_proc()

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_pipenv.pipenv.shutil.which", return_value="/usr/bin/pipenv"),
        patch.object(pipenv, "run", side_effect=fake_run),
    ):
        pipenv.apply_pipenv(_pipenv_ws(tmp_path))

    assert "6.9.0" in lock_path.read_text(encoding="utf-8")
    assert manifest_path.read_bytes() == manifest_before


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


# ---------------------------------------------------------------------------
# pdm / poetry — identical clamp-down shape (TOML lock, `<mgr> add`, `pyproject.toml`
# manifest); pdm / pipenv / poetry — identical raise-on-clamp-failure shape.
# ---------------------------------------------------------------------------

_CLAMP_SUCCESS_CASES = [
    pytest.param(
        "repo_ops_py_pdm.pdm",
        "apply_pdm",
        _pdm_ws,
        "pdm.lock",
        ["pdm", "add"],
        "repo_ops_py_pdm.pdm.shutil.which",
        "/usr/bin/pdm",
        id="pdm",
    ),
    pytest.param(
        "repo_ops_py_poetry.poetry",
        "apply_poetry",
        _poetry_ws,
        "poetry.lock",
        ["poetry", "add"],
        "repo_ops_py_poetry.poetry.shutil.which",
        "/usr/bin/poetry",
        id="poetry",
    ),
]

_CLAMP_FAIL_CASES = [
    pytest.param(
        "repo_ops_py_pdm.pdm",
        "apply_pdm",
        _pdm_ws,
        "pdm.lock",
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n',
        "repo_ops_py_pdm.pdm.shutil.which",
        "/usr/bin/pdm",
        id="pdm",
    ),
    pytest.param(
        "repo_ops_py_pipenv.pipenv",
        "apply_pipenv",
        _pipenv_ws,
        "Pipfile.lock",
        json.dumps({"default": {"typescript-stub": {"version": "==7.2.0"}}}),
        "repo_ops_py_pipenv.pipenv.shutil.which",
        "/usr/bin/pipenv",
        id="pipenv",
    ),
    pytest.param(
        "repo_ops_py_poetry.poetry",
        "apply_poetry",
        _poetry_ws,
        "poetry.lock",
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n',
        "repo_ops_py_poetry.poetry.shutil.which",
        "/usr/bin/poetry",
        id="poetry",
    ),
]


@pytest.mark.parametrize(
    (
        "module_path",
        "apply_fn_name",
        "ws_factory",
        "lock_filename",
        "add_cmd_prefix",
        "which_target",
        "which_binary",
    ),
    _CLAMP_SUCCESS_CASES,
)
def test_apply_clamps_package_above_ceiling(
    tmp_path: Path,
    make_proc,
    module_path: str,
    apply_fn_name: str,
    ws_factory,
    lock_filename: str,
    add_cmd_prefix: list[str],
    which_target: str,
    which_binary: str,
) -> None:
    module = importlib.import_module(module_path)
    lock_path = tmp_path / lock_filename
    lock_path.write_text(
        '[[package]]\nname = "typescript-stub"\nversion = "7.2.0"\n', encoding="utf-8"
    )
    manifest_path = tmp_path / "pyproject.toml"
    manifest_before = b"[project]\nname = 'demo'\n"
    manifest_path.write_bytes(manifest_before)

    def fake_run(cmd, cwd, timeout=None):
        if cmd[: len(add_cmd_prefix)] == add_cmd_prefix:
            manifest_path.write_bytes(b"[project]\nname = 'changed'\n")
            lock_path.write_text(
                '[[package]]\nname = "typescript-stub"\nversion = "6.9.0"\n',
                encoding="utf-8",
            )
        return make_proc()

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch(which_target, return_value=which_binary),
        patch.object(module, "run", side_effect=fake_run),
    ):
        getattr(module, apply_fn_name)(ws_factory(tmp_path))

    assert "6.9.0" in lock_path.read_text(encoding="utf-8")
    assert manifest_path.read_bytes() == manifest_before


@pytest.mark.parametrize(
    (
        "module_path",
        "apply_fn_name",
        "ws_factory",
        "lock_filename",
        "lock_content",
        "which_target",
        "which_binary",
    ),
    _CLAMP_FAIL_CASES,
)
def test_apply_raises_when_clamp_fails(
    tmp_path: Path,
    make_proc,
    module_path: str,
    apply_fn_name: str,
    ws_factory,
    lock_filename: str,
    lock_content: str,
    which_target: str,
    which_binary: str,
) -> None:
    module = importlib.import_module(module_path)
    (tmp_path / lock_filename).write_text(lock_content, encoding="utf-8")

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch(which_target, return_value=which_binary),
        patch.object(module, "run", return_value=make_proc()),
        pytest.raises(RuntimeError, match="hold"),
    ):
        getattr(module, apply_fn_name)(ws_factory(tmp_path))


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


def test_apply_pip_tools_passes_hold_constraints_file(tmp_path: Path, make_proc) -> None:
    from repo_ops_py_pip_tools import pip_tools

    (tmp_path / "requirements.in").write_text("typescript-stub\nother-pkg\n", encoding="utf-8")
    captured_cmds: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured_cmds.append(cmd)
        return make_proc(returncode=0)

    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
        patch("repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"),
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

    (tmp_path / "requirements.in").write_text("typescript-stub\nother-pkg\n", encoding="utf-8")
    ws = _pip_tools_ws(tmp_path)
    with (
        patch.object(
            core_holds,
            "_global_holds_cached",
            return_value=(_held_rule("typescript-stub"),),
        ),
        patch.object(core_holds, "_repo_holds_cached", return_value=()),
    ):
        with pip_tools._hold_constraints_file(ws, tmp_path / "requirements.in") as constraints_path:
            assert constraints_path is not None
            content = constraints_path.read_text(encoding="utf-8")
            assert "typescript-stub<=6.999999.999999" in content
            assert "other-pkg" not in content
        assert not constraints_path.exists()
