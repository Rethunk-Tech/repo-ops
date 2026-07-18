"""check_hatch must not create .dep_refresh_venv in the workspace (#70)."""

from __future__ import annotations

import subprocess
from pathlib import Path
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
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
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert marker.exists()
    assert legacy.is_dir()


def test_check_hatch_raises_on_pip_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Failed ``uv pip install`` must surface RuntimeError, not silently return []."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            venv.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="No matching distribution found"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        pytest.raises(RuntimeError, match="No matching distribution found"),
    ):
        check_hatch(ws)


def test_check_hatch_raises_on_venv_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Failed ``uv venv`` must also surface RuntimeError."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="permission denied"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        pytest.raises(RuntimeError, match="permission denied"),
    ):
        check_hatch(ws)


def test_install_target_picks_test_when_dev_absent(tmp_path: Path):
    """Extras detection must prefer ``[test]`` over hardcoded ``[dev]`` when dev is absent."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        "[project.optional-dependencies]\n"
        'test = ["pytest"]\n'
        'lint = ["ruff"]\n',
        encoding="utf-8",
    )
    assert _install_target(tmp_path) == ".[test]"


def test_install_target_prefers_dev(tmp_path: Path):
    """When both ``dev`` and ``test`` exist, ``dev`` is preferred."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        "[project.optional-dependencies]\n"
        'dev = ["pytest", "ruff"]\n'
        'test = ["pytest"]\n',
        encoding="utf-8",
    )
    assert _install_target(tmp_path) == ".[dev]"


def test_install_target_falls_back_to_bare_dot(tmp_path: Path):
    """No matching optional-dependencies → plain ``"."`` (no extras)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    assert _install_target(tmp_path) == "."


def test_check_hatch_raises_on_pip_list_outdated_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Failed ``uv pip list --outdated`` must raise RuntimeError, not silently return []."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def fake_run(cmd, cwd, timeout=600, env=None):
        if cmd[:2] == ["uv", "venv"]:
            venv = Path(cmd[2])
            venv.mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "list"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="uv pip list failed: network error"
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        pytest.raises(RuntimeError, match="uv pip list failed: network error"),
    ):
        check_hatch(ws)


def test_check_hatch_uses_detected_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """check_hatch passes the detected extras group to ``uv pip install``."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
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
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

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
    """#66 — a uv.lock project with uv absent from PATH must warn + return [], not crash."""
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


def test_check_uv_lock_passes_timeout_check(tmp_path: Path) -> None:
    """#70 — check_uv_lock must call ``run`` with ``timeout=TIMEOUT_CHECK``."""
    ws = _make_ws(tmp_path)
    captured_timeout: list[int | None] = []

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        captured_timeout.append(timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("repo_ops_py.uv_lock.run", fake_run):
        check_uv_lock(ws)

    assert captured_timeout == [TIMEOUT_CHECK]


def test_check_hatch_pip_install_uses_timeout_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#93 — venv-path ``uv pip install -e`` must use TIMEOUT_CHECK, not hardcoded 300."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
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
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert install_timeout == [TIMEOUT_CHECK], (
        f"uv pip install called with timeout={install_timeout}, expected TIMEOUT_CHECK={TIMEOUT_CHECK}"
    )


# ---------------------------------------------------------------------------
# Issue #132 — gate uv.lock creation behind REPO_OPS_HATCH_MIGRATE_UV_LOCK
# ---------------------------------------------------------------------------


def test_apply_hatch_no_uv_lock_no_flag_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#132 — no uv.lock + flag unset → apply_hatch returns without creating uv.lock."""
    monkeypatch.delenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)
    run_calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: object, timeout: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        run_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        patch("repo_ops_py.uv_lock.run", fake_run),
    ):
        apply_hatch(ws)

    assert not (tmp_path / "uv.lock").exists(), "uv.lock must not be created"
    assert run_calls == [], f"no subprocess must run; got {run_calls}"


def test_apply_hatch_no_uv_lock_with_flag_runs_uv_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#132 — no uv.lock + flag set → apply_hatch runs uv lock then uv lock --upgrade."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)
    run_calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: object, timeout: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        run_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        patch("repo_ops_py.uv_lock.run", fake_run),
    ):
        apply_hatch(ws)

    assert run_calls[0] == ["uv", "lock"], (
        f"first call must be uv lock; got {run_calls}"
    )
    assert ["uv", "lock", "--upgrade"] in run_calls, (
        f"uv lock --upgrade must run; got {run_calls}"
    )


def test_check_hatch_no_uv_lock_no_migrate_skips_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F002 — no uv.lock + migrate unset → [] + migrate-needed WARN (no temp-venv drift)."""
    monkeypatch.delenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    def boom_run(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("run() must not be called when migrate is unset")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", boom_run),
    ):
        assert check_hatch(ws) == []

    err = capsys.readouterr().err
    assert "no uv.lock" in err
    assert "REPO_OPS_HATCH_MIGRATE_UV_LOCK" in err or "migrate-to-uv-lock" in err


def test_check_hatch_no_uv_warns_without_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """F152 — no uv.lock + migrate set + uv absent → WARN + []."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
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
    """F152 — apply_hatch with uv absent → WARN and return (no raise)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)

    with patch("repo_ops_py_hatch.hatch.shutil.which", return_value=None):
        apply_hatch(ws)

    assert "without uv on PATH" in capsys.readouterr().err


def test_apply_hatch_with_existing_uv_lock_runs_apply_uv_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F154 — existing uv.lock → skip uv lock creation, run apply_uv_lock only."""
    monkeypatch.delenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", raising=False)
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
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
        patch("repo_ops_py.uv_lock.run", fake_run),
    ):
        apply_hatch(ws)

    assert ["uv", "lock"] not in run_calls, f"must not create lock; got {run_calls}"
    assert ["uv", "lock", "--upgrade"] in run_calls
    assert ["uv", "sync"] in run_calls


def test_check_hatch_with_uv_lock_delegates_to_check_uv_lock(
    tmp_path: Path,
) -> None:
    """F155 — uv.lock present → check_uv_lock (uv lock --upgrade --dry-run)."""
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
        return subprocess.CompletedProcess(
            cmd, 0, stdout="would upgrade foo 1.0 -> 2.0\n", stderr=""
        )

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py.uv_lock.run", fake_run),
    ):
        changes = check_hatch(ws)

    assert run_calls == [["uv", "lock", "--upgrade", "--dry-run"]]
    assert len(changes) == 1
    assert changes[0].name == "foo"


def test_check_hatch_venv_and_list_use_timeout_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F151 — uv venv and uv pip list --outdated use resolve_timeout_check."""
    monkeypatch.setenv("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    ws = _make_ws(tmp_path)
    timeouts: dict[str, int | None] = {}

    def fake_run(
        cmd: list[str], cwd: Path, timeout: int | None = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["uv", "venv"]:
            Path(cmd[2]).mkdir(parents=True, exist_ok=True)
            timeouts["venv"] = timeout
        elif cmd[:3] == ["uv", "pip", "install"]:
            timeouts["install"] = timeout
        elif cmd[:3] == ["uv", "pip", "list"]:
            timeouts["list"] = timeout
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("repo_ops_py_hatch.hatch.shutil.which", return_value="/usr/bin/uv"),
        patch("repo_ops_py_hatch.hatch.run", fake_run),
    ):
        check_hatch(ws)

    assert timeouts.get("venv") == TIMEOUT_CHECK
    assert timeouts.get("install") == TIMEOUT_CHECK
    assert timeouts.get("list") == TIMEOUT_CHECK
