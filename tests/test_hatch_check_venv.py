"""check_hatch must not create .dep_refresh_venv in the workspace."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_models import Workspace
from repo_ops_py.uv_lock import check_uv_lock
from repo_ops_py_hatch.hatch import (
    _install_target,
    apply_hatch,
    check_hatch,
)


def _make_ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="py",
        manager="hatch",
        git_repo=tmp_path,
        label="",
    )


def test_check_hatch_uses_temp_venv_not_workspace_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    legacy = tmp_path / ".dep_refresh_venv"
    legacy.mkdir()
    marker = legacy / "keep-me"
    marker.write_text("preserve", encoding="utf-8")

    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            assert venv.parent != tmp_path
            venv.mkdir(parents=True, exist_ok=True)
        return make_proc()

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert marker.exists()
    assert legacy.is_dir()


def test_check_hatch_raises_on_pip_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
):
    """Failed ``uv pip install`` must surface RuntimeError, not silently return []."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            venv.mkdir(parents=True, exist_ok=True)
            return make_proc()
        if cmd[:3] == ["uv", "pip", "install"]:
            return make_proc(returncode=1, stderr="No matching distribution found")
        return make_proc()

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        pytest.raises(RuntimeError, match="No matching distribution found"),
    ):
        check_hatch(ws)


def test_check_hatch_raises_on_venv_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
):
    """Failed ``uv venv`` must also surface RuntimeError."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            return make_proc(returncode=1, stderr="permission denied")
        return make_proc()

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        pytest.raises(RuntimeError, match="permission denied"),
    ):
        check_hatch(ws)


@pytest.mark.parametrize(
    ("optional_deps", "expected"),
    [
        pytest.param('test = ["pytest"]\nlint = ["ruff"]\n', ".[test]", id="test_over_lint"),
        pytest.param(
            'dev = ["pytest", "ruff"]\ntest = ["pytest"]\n',
            ".[dev]",
            id="dev_over_test",
        ),
        pytest.param("", ".", id="no_extras"),
    ],
)
def test_install_target_extras_detection(tmp_path: Path, optional_deps: str, expected: str) -> None:
    """Extras detection prefers ``[dev]`` over ``[test]``, and falls back to bare ``.`` with neither."""
    header = '[project]\nname = "demo"\nversion = "0.1.0"\n'
    section = "[project.optional-dependencies]\n" + optional_deps if optional_deps else ""
    (tmp_path / "pyproject.toml").write_text(header + section, encoding="utf-8")
    assert _install_target(tmp_path) == expected


def test_check_hatch_raises_on_pip_list_outdated_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
):
    """Failed ``uv pip list --outdated`` must raise RuntimeError, not silently return []."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            venv.mkdir(parents=True, exist_ok=True)
            return make_proc()
        if cmd[:3] == ["uv", "pip", "install"]:
            return make_proc()
        if cmd[:3] == ["uv", "pip", "list"]:
            return make_proc(returncode=1, stderr="uv pip list failed: network error")
        return make_proc()

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        pytest.raises(RuntimeError, match="uv pip list failed: network error"),
    ):
        check_hatch(ws)


def test_check_hatch_uses_detected_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
):
    """check_hatch passes the detected extras group to ``uv pip install``."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        "[project.optional-dependencies]\n"
        'test = ["pytest"]\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)
    install_calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            venv.mkdir(parents=True, exist_ok=True)
        if cmd[:3] == ["uv", "pip", "install"]:
            install_calls.append(list(cmd))
        return make_proc()

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert install_calls, "uv pip install was never called"
    assert ".[test]" in install_calls[0], f"Expected .[test] in {install_calls[0]}"


def test_check_hatch_uv_lock_without_uv_warns_and_returns_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """A uv.lock project with uv absent from PATH must warn + return [], not crash."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    ws = _make_ws(tmp_path)

    def boom_run(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("run() must not be called when uv is missing")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value=None),
        patch("repo_ops_py_hatch.hatch.run", boom_run),
    ):
        assert check_hatch(ws) == []

    assert "without uv on PATH" in capsys.readouterr().err


def test_check_uv_lock_passes_timeout_check(tmp_path: Path, make_proc) -> None:
    """check_uv_lock must call ``run`` with ``timeout=TIMEOUT_CHECK``."""
    ws = _make_ws(tmp_path)
    captured_timeout: list[int | None] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        captured_timeout.append(timeout)
        return make_proc()

    with patch("repo_ops_py.uv_lock.run", fake_run):
        check_uv_lock(ws)

    assert captured_timeout == [TIMEOUT_CHECK]


def test_check_hatch_pip_install_uses_timeout_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc
) -> None:
    """Venv-path ``uv pip install -e`` must use TIMEOUT_CHECK, not hardcoded 300."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)
    install_timeout: list[int | None] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            venv.mkdir(parents=True, exist_ok=True)
        if cmd[:3] == ["uv", "pip", "install"]:
            install_timeout.append(timeout)
        return make_proc()

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert install_timeout == [TIMEOUT_CHECK], (
        f"uv pip install called with timeout={install_timeout}, expected TIMEOUT_CHECK={TIMEOUT_CHECK}"
    )


# ---------------------------------------------------------------------------
# uv.lock creation for hatch projects without one
# ---------------------------------------------------------------------------


def test_apply_hatch_no_uv_lock_with_flag_runs_uv_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_proc, fake_run: Any
) -> None:
    """No uv.lock + flag set → apply_hatch runs uv lock then uv lock --upgrade."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        patch("repo_ops_py.uv_lock.run", fake_run),
    ):
        apply_hatch(ws)

    assert fake_run.calls[0] == ["uv", "lock"], f"first call must be uv lock; got {fake_run.calls}"
    assert ["uv", "lock", "--upgrade"] in fake_run.calls, (
        f"uv lock --upgrade must run; got {fake_run.calls}"
    )


def test_check_hatch_no_uv_warns_without_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No uv.lock + migrate set + uv absent → WARN + []."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    with patch("repo_ops_py_hatch.hatch.shutil.which", return_value=None):
        assert check_hatch(ws) == []

    assert "without uv on PATH" in capsys.readouterr().err


def test_apply_hatch_no_uv_warns_and_returns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """apply_hatch with uv absent → WARN and return (no raise)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    with patch("repo_ops_py_hatch.hatch.shutil.which", return_value=None):
        apply_hatch(ws)

    assert "without uv on PATH" in capsys.readouterr().err


def test_check_hatch_with_uv_lock_delegates_to_check_uv_lock(tmp_path: Path, make_proc) -> None:
    """uv.lock present → check_uv_lock (uv lock --upgrade --dry-run)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    ws = _make_ws(tmp_path)
    run_calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: object, timeout: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        run_calls.append(list(cmd))
        return make_proc(stdout="would upgrade foo 1.0 -> 2.0\n")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py.uv_lock.run", fake_run),
    ):
        changes = check_hatch(ws)

    assert run_calls == [["uv", "lock", "--upgrade", "--dry-run"]]
    assert len(changes) == 1
    assert changes[0].name == "foo"
