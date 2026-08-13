from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_models import Change, Workspace
from repo_ops_py_pip_tools.pip_tools import (
    _pip_tools_requirement_satisfied,
    apply_pip_tools,
    check_pip_tools,
)


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="python",
        manager="pip-tools",
        git_repo=tmp_path,
        label="demo",
    )


def _seed_workspace(tmp_path: Path) -> None:
    (tmp_path / "requirements.in").write_text(
        "requests==2.28.0\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "requests==2.31.0\n",
        encoding="utf-8",
    )


def test_pip_tools_requirement_preserves_exact_pin_semantics() -> None:
    assert _pip_tools_requirement_satisfied("2.31.0", "==2.31.0") is True
    assert _pip_tools_requirement_satisfied("3.0.0", "==2.31.0") is False
    assert _pip_tools_requirement_satisfied("3.0.0", ">=2.31.0") is True


def test_check_pip_tools_reports_direct_constraint_bump(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)

    def fake_run(cmd, cwd, timeout=None):
        return type(
            "Proc",
            (),
            {"returncode": 0, "stdout": "requests==2.31.0\n", "stderr": ""},
        )()

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            return_value="/usr/bin/uv",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pip_tools.pip_tools._pip_tools_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools._pypi_latest",
            return_value="3.0.0",
        ),
    ):
        result = check_pip_tools(_ws(tmp_path))

    in_range, behind = result
    assert in_range == []
    assert behind == [Change("requests", "2.31.0", "3.0.0")]


def test_apply_pip_tools_restores_files_when_targeted_compile_fails(
    tmp_path: Path,
) -> None:
    _seed_workspace(tmp_path)
    req_in = tmp_path / "requirements.in"
    req_txt = tmp_path / "requirements.txt"
    in_before = req_in.read_bytes()
    txt_before = req_txt.read_bytes()
    candidate = Change("requests", "2.31.0", "3.0.0")

    def fake_run(cmd, cwd, timeout=None):
        if "--upgrade-package" in cmd:
            req_in.write_text("requests==3.0.0\n", encoding="utf-8")
            req_txt.write_text("changed\n", encoding="utf-8")
            return type(
                "Proc", (), {"returncode": 1, "stdout": "", "stderr": "failed"}
            )()
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: None if name == "uv" else "/usr/bin/pip-compile",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pip_tools.pip_tools._pip_tools_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools._pip_tools_latest_direct_changes",
            return_value=[candidate],
        ),
    ):
        try:
            apply_pip_tools(_ws(tmp_path))
        except RuntimeError as exc:
            assert "failed" in str(exc)
        else:
            raise AssertionError("expected targeted compile failure")

    assert req_in.read_bytes() == in_before
    assert req_txt.read_bytes() == txt_before


def test_apply_pip_tools_uses_targeted_upgrade_package(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    candidate = Change("requests", "2.31.0", "3.0.0")
    calls: list[list[str]] = []

    def fake_run(cmd, cwd, timeout=None):
        calls.append(list(cmd))
        output = Path(cmd[cmd.index("-o") + 1])
        if "--upgrade-package" in cmd:
            output.write_text("requests==3.0.0\n", encoding="utf-8")
        else:
            output.write_text("requests==2.31.0\n", encoding="utf-8")
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with (
        patch(
            "repo_ops_py_pip_tools.pip_tools.shutil.which",
            side_effect=lambda name: None if name == "uv" else "/usr/bin/pip-compile",
        ),
        patch("repo_ops_py_pip_tools.pip_tools.run", side_effect=fake_run),
        patch(
            "repo_ops_py_pip_tools.pip_tools._pip_tools_targeted_bump_enabled",
            return_value=True,
        ),
        patch(
            "repo_ops_py_pip_tools.pip_tools._pip_tools_latest_direct_changes",
            return_value=[candidate],
        ),
    ):
        remaining = apply_pip_tools(_ws(tmp_path))

    assert remaining == []
    assert calls[1][0:4] == [
        "pip-compile",
        "--upgrade",
        "--upgrade-package",
        "requests==3.0.0",
    ]
    assert (tmp_path / "requirements.in").read_text() == "requests==3.0.0\n"
