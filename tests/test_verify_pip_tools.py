"""Regression #99: verify_workspace must dispatch to pip-tools branch (not silently return None)."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from repo_ops_upgrade.verify import verify_workspace


def _fake_proc(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CompletedProcess[str]:
    return CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_pip_tools_verify_detects_drift(tmp_path: Path, make_workspace) -> None:
    """verify_workspace must return an error string when requirements.txt drifts from compiled output."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n", encoding="utf-8")
    # requirements.txt pins an older version
    req_txt = tmp_path / "requirements.txt"
    req_txt.write_text("requests==2.28.0\n", encoding="utf-8")

    # compiled output proposes a newer version — drift detected
    compiled = "requests==2.31.0\n"

    with (
        patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_upgrade.verify.run",
            return_value=_fake_proc(0, compiled),
        ),
    ):
        result = verify_workspace(make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo"), verify_test=False)

    assert result is not None, "expected a drift error, got None (silent pass)"
    assert "requests" in result


def test_pip_tools_verify_passes_when_in_sync(tmp_path: Path, make_workspace) -> None:
    """verify_workspace must return None when requirements.txt matches compiled output."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n", encoding="utf-8")
    req_txt = tmp_path / "requirements.txt"
    req_txt.write_text("requests==2.31.0\n", encoding="utf-8")

    compiled = "requests==2.31.0\n"

    with (
        patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_upgrade.verify.run",
            return_value=_fake_proc(0, compiled),
        ),
    ):
        result = verify_workspace(make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo"), verify_test=False)

    assert result is None, f"expected None (in sync), got: {result!r}"


def test_pip_tools_verify_compile_failure_returns_error(tmp_path: Path, make_workspace) -> None:
    """verify_workspace must return the compile error string when uv pip compile fails."""
    req_in = tmp_path / "requirements.in"
    req_in.write_text("requests\n", encoding="utf-8")

    with (
        patch("repo_ops_upgrade.verify.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "repo_ops_upgrade.verify.run",
            return_value=_fake_proc(1, "", "error: could not compile"),
        ),
    ):
        result = verify_workspace(make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo"), verify_test=False)

    assert result is not None, "expected an error on compile failure, got None"
    assert "compile" in result or "error" in result.lower()


def test_pip_tools_verify_no_req_in_skips_silently(tmp_path: Path, make_workspace) -> None:
    """verify_workspace must return None when requirements.in is absent (no-op gracefully)."""
    result = verify_workspace(make_workspace(tmp_path, ecosystem="python", manager="pip-tools", label="demo"), verify_test=False)
    assert result is None
