"""Tests for pip-tools check/apply (#54 — check must use --upgrade)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_py_pip_tools.pip_tools import (
    _compile_requirements,
    apply_pip_tools,
    check_pip_tools,
)


def test_check_pip_tools_uv_path_uses_upgrade(tmp_path: Path, make_workspace) -> None:
    """#54 — uv pip compile in check must include --upgrade so proposed reflects apply."""
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
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        check_pip_tools(
            make_workspace(
                tmp_path, ecosystem="python", manager="pip-tools", label="demo"
            )
        )

    assert len(captured) == 1
    assert "--upgrade" in captured[0]


def test_check_pip_tools_pip_compile_fallback_dry_run_uses_upgrade(
    tmp_path: Path,
    make_workspace,
) -> None:
    """#73 — pip-compile fallback dry-run in check must include --upgrade so plan matches apply."""
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
            make_workspace(
                tmp_path, ecosystem="python", manager="pip-tools", label="demo"
            )
        )

    assert len(captured) == 1
    assert "--upgrade" in captured[0]


def test_check_pip_tools_diffs_against_existing_requirements_txt(
    tmp_path: Path,
    monkeypatch,
    make_workspace,
    make_proc,
) -> None:
    """Existing requirements.txt pins are read as 'current' and diffed against
    the freshly compiled 'proposed' set (exercises the req_txt.exists() branch)."""
    monkeypatch.setenv("REPO_OPS_PIP_TOOLS_TARGETED_BUMP", "0")
    (tmp_path / "requirements.in").write_text("requests\n")
    (tmp_path / "requirements.txt").write_text("requests==2.28.0\n")

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools.run",
            return_value=make_proc(0, stdout="requests==2.31.0\n"),
        ),
    ):
        changes = check_pip_tools(
            make_workspace(
                tmp_path, ecosystem="python", manager="pip-tools", label="demo"
            )
        )

    assert len(changes) == 1
    assert changes[0].name == "requests"
    assert changes[0].from_ver == "2.28.0"
    assert changes[0].to == "2.31.0"


def test_check_pip_tools_uv_empty_stdout_falls_through_to_pip_compile(
    tmp_path: Path,
    monkeypatch,
    make_workspace,
    make_proc,
) -> None:
    """uv returncode 0 with empty stdout is treated like 'no proposal yet' and
    falls through to the pip-compile branch, not an error."""
    monkeypatch.setenv("REPO_OPS_PIP_TOOLS_TARGETED_BUMP", "0")
    (tmp_path / "requirements.in").write_text("requests\n")

    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(cmd)
        if cmd[0] == "uv":
            return make_proc(0, stdout="")
        return make_proc(0, stdout="requests==2.31.0\n")

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            return_value="/usr/bin/tool",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        changes = check_pip_tools(
            make_workspace(
                tmp_path, ecosystem="python", manager="pip-tools", label="demo"
            )
        )

    assert calls[0][0] == "uv"
    assert calls[1][0] == "pip-compile"
    assert any(c.name == "requests" for c in changes)


def test_check_pip_tools_no_tools_available_returns_empty(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Neither uv nor pip-compile installed: quiet no-op, not an error (uv was
    never attempted so uv_failed stays False)."""
    monkeypatch.setenv("REPO_OPS_PIP_TOOLS_TARGETED_BUMP", "0")
    (tmp_path / "requirements.in").write_text("requests\n")

    with patch("repo_ops_py_pip_tools.pip_tools.shutil.which", return_value=None):
        assert (
            check_pip_tools(
                make_workspace(
                    tmp_path, ecosystem="python", manager="pip-tools", label="demo"
                )
            )
            == []
        )


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
            make_workspace(
                tmp_path, ecosystem="python", manager="pip-tools", label="demo"
            )
        )

    assert "WARN uv pip compile failed: boom" in capsys.readouterr().err


def test_check_pip_tools_uv_fails_falls_through_to_pip_compile_with_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
    make_workspace,
    make_proc,
) -> None:
    """uv failure still falls through to pip-compile (existing fallback preserved),
    but now emits a WARN so the failure is not silently swallowed."""
    monkeypatch.setenv("REPO_OPS_PIP_TOOLS_TARGETED_BUMP", "0")
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n")

    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(cmd)
        if cmd[0] == "uv":
            return make_proc(1, stderr="uv exploded")
        return make_proc(0, stdout="requests==2.31.0\n")

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            return_value="/usr/bin/tool",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
    ):
        changes = check_pip_tools(
            make_workspace(
                tmp_path, ecosystem="python", manager="pip-tools", label="demo"
            )
        )

    assert "WARN uv pip compile failed: uv exploded" in capsys.readouterr().err
    assert any(c.name == "requests" for c in changes)
    assert calls[0][0] == "uv"


def test_compile_requirements_uv_success_returns_early(
    tmp_path: Path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    out = tmp_path / "requirements.txt"
    req_in = tmp_path / "requirements.in"

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which", return_value="/usr/bin/uv"
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools.run", return_value=make_proc(0)
        ) as mock_run,
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


def test_apply_pip_tools_missing_requirements_in_raises(
    tmp_path: Path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo")
    with pytest.raises(RuntimeError, match=r"requirements\.in not found"):
        apply_pip_tools(ws)


def test_apply_pip_tools_uses_existing_requirements_txt(
    tmp_path: Path, make_workspace
) -> None:
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
