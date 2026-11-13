"""Tests for pip-tools check/apply."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from repo_ops_core.holds import HoldRule
from repo_ops_models import Workspace
from repo_ops_py_pip_tools.pip_tools import (
    _compile_requirements,
    _pip_tools_bumped_requirement,
    _pip_tools_latest_direct_changes,
    apply_pip_tools,
    check_pip_tools,
)


def test_check_pip_tools_uv_path_uses_upgrade(tmp_path: Path, make_workspace) -> None:
    """Uv pip compile in check must include --upgrade so proposed reflects apply."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n")

    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(cmd)
        # Return a minimal compiled output
        return type(
            "Proc",
            (),
            {"returncode": 0, "stdout": "requests==2.31.0\n", "stderr": ""},
        )()

    with (
        patch("repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        check_pip_tools(
            make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
        )

    assert len(captured) == 1
    assert "--upgrade" in captured[0]


def test_check_pip_tools_pip_compile_fallback_dry_run_uses_upgrade(
    tmp_path: Path,
    make_workspace,
) -> None:
    """Pip-compile fallback dry-run in check must include --upgrade so plan matches apply."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n")

    captured: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        captured.append(cmd)
        return type(
            "Proc",
            (),
            {"returncode": 0, "stdout": "requests==2.31.0\n", "stderr": ""},
        )()

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: None if name == "uv" else "/usr/bin/pip-compile",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        check_pip_tools(
            make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
        )

    assert len(captured) == 1
    assert "--upgrade" in captured[0]


def test_check_pip_tools_uv_fails_pip_compile_missing_raises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_workspace, make_proc
) -> None:
    """uv pip compile failing with no pip-compile fallback must raise, not silently
    return an empty change list (previous behaviour was inconsistent with the
    pip-compile-only branch, which already raised on the equivalent failure)."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n")

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: "/usr/bin/uv" if name == "uv" else None,
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools.run",
            return_value=make_proc(1, stderr="boom"),
        ),
        pytest.raises(RuntimeError, match="pip-compile not on PATH"),
    ):
        check_pip_tools(
            make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
        )

    assert "WARN uv pip compile failed: boom" in capsys.readouterr().err


def test_compile_requirements_uv_success_returns_early(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    out = tmp_path / "requirements.txt"
    req_in = tmp_path / "requirements.in"

    with (
        patch("repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_pip_tools.pip_tools.run", return_value=make_proc(0)) as mock_run,
    ):
        _compile_requirements(ws, out, req_in)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][:3] == ["uv", "pip", "compile"]


def test_compile_requirements_uv_fails_pip_compile_missing_raises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    out = tmp_path / "requirements.txt"
    req_in = tmp_path / "requirements.in"

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: "/usr/bin/uv" if name == "uv" else None,
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools.run",
            return_value=make_proc(1, stderr="uv broke"),
        ),
        pytest.raises(RuntimeError, match="pip-compile not on PATH"),
    ):
        _compile_requirements(ws, out, req_in)

    assert "WARN uv pip compile failed: uv broke" in capsys.readouterr().err


def test_compile_requirements_uv_fails_pip_compile_succeeds(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    out = tmp_path / "requirements.txt"
    req_in = tmp_path / "requirements.in"

    def fake_run(cmd, cwd, timeout=None):
        if cmd[0] == "uv":
            return make_proc(1, stderr="uv broke")
        return make_proc(0)

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            return_value="/usr/bin/tool",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        _compile_requirements(ws, out, req_in)  # must not raise


def test_compile_requirements_pip_compile_fails_raises(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    out = tmp_path / "requirements.txt"
    req_in = tmp_path / "requirements.in"

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: None if name == "uv" else "/usr/bin/pip-compile",
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools.run",
            return_value=make_proc(1, stderr="pip-compile broke"),
        ),
        pytest.raises(RuntimeError, match="pip-compile broke"),
    ):
        _compile_requirements(ws, out, req_in)


def test_exclusive_bound_includes_latest_and_hold_skips_the_lift(
    tmp_path: Path, make_workspace
) -> None:
    """`<82` must become `<=84.0.0`, and a hold below latest must leave the pin."""
    assert _pip_tools_bumped_requirement("<82", "84.0.0") == "<=84.0.0"
    assert _pip_tools_bumped_requirement(">1", "2.0.0") == ">=2.0.0"
    assert _pip_tools_bumped_requirement("<=81", "81.0.1") == "<=81.0.1"

    req_in = tmp_path / "requirements.in"
    req_in.write_text("setuptools<82\n")
    (tmp_path / "requirements.txt").write_text("setuptools==81.0.0\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="pip-tools", label="demo")
    held = HoldRule(package="setuptools", max_version="81", ecosystem="py")

    with (
        patch("repo_ops_py_pip_tools.pip_tools._compile_requirements"),
        patch("repo_ops_py_pip_tools.pip_tools._pypi_latest", return_value="84.0.0"),
        patch("repo_ops_py_pip_tools.pip_tools.resolve_package_hold", return_value=held),
    ):
        apply_pip_tools(ws)

    assert req_in.read_text() == "setuptools<82\n"


def test_pip_tools_compatible_release_is_not_a_direct_change(
    tmp_path: Path, make_workspace
) -> None:
    (tmp_path / "requirements.in").write_text("requests~=2.31\n")
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
    ws = make_workspace(tmp_path, ecosystem="py", manager="pip-tools", label="demo")
    with patch("repo_ops_py_pip_tools.pip_tools._pypi_latest", return_value="2.32.0"):
        assert _pip_tools_latest_direct_changes(ws) == []


def test_apply_pip_tools_missing_requirements_in_raises(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    with pytest.raises(RuntimeError, match=r"requirements\.in not found"):
        apply_pip_tools(ws)


def test_apply_pip_tools_uses_existing_requirements_txt(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    (tmp_path / "requirements.in").write_text("requests\n")
    (tmp_path / "requirements.txt").write_text("requests==2.30.0\n")

    with patch("repo_ops_py_pip_tools.pip_tools._compile_requirements") as mock_compile:
        apply_pip_tools(ws)

    args = mock_compile.call_args[0]
    assert args[1] == tmp_path / "requirements.txt"
    assert args[2] == tmp_path / "requirements.in"


def test_apply_pip_tools_derives_txt_path_when_no_existing_txt(
    tmp_path: Path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    (tmp_path / "requirements.in").write_text("requests\n")

    with patch("repo_ops_py_pip_tools.pip_tools._compile_requirements") as mock_compile:
        apply_pip_tools(ws)

    args = mock_compile.call_args[0]
    assert args[1] == tmp_path / "requirements.txt"


def test_apply_targeted_bump_unresolvable_on_index_keeps_old_pin(tmp_path: Path) -> None:
    """A bump the pinned index cannot resolve is reported behind-latest, not a workspace failure."""
    ws = Workspace(
        path=tmp_path, ecosystem="py", manager="pip-tools", git_repo=tmp_path, label="demo"
    )
    (tmp_path / "requirements.in").write_text("torch==2.14.1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("torch==2.14.1\n", encoding="utf-8")
    calls = iter([0, 1])

    def fake_run(cmd, cwd, timeout=None):
        rc = next(calls)
        return SimpleNamespace(returncode=rc, stdout="", stderr="no version of torch==2.15.0")

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda n: "/usr/bin/uv" if n == "uv" else None,
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
        patch("repo_ops_py_pip_tools.pip_tools._pypi_latest", return_value="2.15.0"),
    ):
        remaining = apply_pip_tools(ws)

    assert [c.name for c in remaining] == ["torch"]
    assert (tmp_path / "requirements.in").read_text() == "torch==2.14.1\n"
    assert (tmp_path / "requirements.txt").read_text() == "torch==2.14.1\n"


_NO_SOLUTION = (
    "  x No solution found when resolving dependencies:\n"
    "  `-> Because there is no version of torch==2.15.0 and you require torch==2.15.0,\n"
    "      we can conclude that your requirements are unsatisfiable.\n"
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_check_pip_tools_holds_unsolvable_pin_and_retries(tmp_path: Path, make_workspace) -> None:
    (tmp_path / "requirements.in").write_text("torch==2.15.0\nrequests\n")
    (tmp_path / "requirements.txt").write_text("torch==2.14.0\nrequests==2.31.0\n")
    seen_constraints: list[str | None] = []

    def fake_run(cmd, cwd, timeout=None):
        if "-c" not in cmd:
            seen_constraints.append(None)
            return _proc(1, stderr=_NO_SOLUTION)
        seen_constraints.append(Path(cmd[cmd.index("-c") + 1]).read_text())
        return _proc(0, stdout="torch==2.14.0\nrequests==2.32.0\n")

    with (
        patch("repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
        patch("repo_ops_py_pip_tools.pip_tools._pypi_latest", return_value=None),
    ):
        result = check_pip_tools(
            make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
        )

    changes = result[0] if isinstance(result, tuple) else result
    assert [(c.name, c.to) for c in changes] == [("requests", "2.32.0")]
    assert seen_constraints[0] is None
    assert "torch==2.14.0" in (seen_constraints[1] or "")
    assert not (tmp_path / ".repo-ops-holds.txt").exists()


def test_compile_requirements_error_carries_uv_stderr(tmp_path: Path, make_workspace) -> None:
    req_in = tmp_path / "requirements.in"
    req_in.write_text("torch==2.15.0\n")
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")

    def fake_which(name: str) -> str | None:
        return "/usr/bin/uv" if name == "uv" else None

    with (
        patch("repo_ops_py_pip_tools.pip_tools.shutil.which", side_effect=fake_which),
        patch(
            "repo_ops_py_pip_tools.pip_tools.run",
            return_value=_proc(1, stderr=_NO_SOLUTION),
        ),
        pytest.raises(RuntimeError, match=r"no version of torch==2\.15\.0"),
    ):
        _compile_requirements(ws, tmp_path / "requirements.txt", req_in)
