"""bun stays current: nested pins, workflow inputs, and the host runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_models import Workspace
from repo_ops_upgrade_remediations_js import remediations


def _ws(path: Path) -> Workspace:
    return Workspace(path=path, ecosystem="node", manager="bun", git_repo=path, label="test")


def _manifest(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"packageManager": f"bun@{version}"}, indent=2) + "\n")


def _proc(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_nested_manifests_outside_the_declared_workspaces_still_move(tmp_path: Path) -> None:
    """A fixture or app dir is nobody's workspace, so nothing else reaches its pin."""
    _manifest(tmp_path / "package.json", "1.4.2")
    fixtures = tmp_path / ".github" / "test-fixtures" / "a" / "package.json"
    _manifest(fixtures, "1.4.3")
    _manifest(tmp_path / "frontend" / "package.json", "1.4.3")

    with (
        patch.object(remediations, "_pm_runtime_version", return_value="1.4.2"),
        patch.object(remediations, "_pm_registry_latest", return_value="1.4.4"),
        patch.object(remediations, "run", return_value=_proc()),
    ):
        assert remediations.detect_pm_pin(_ws(tmp_path), {}) is True
        ok, detail = remediations.apply_pm_pin(_ws(tmp_path), {})

    assert ok is True
    assert "3 manifest(s)" in detail
    for path in (tmp_path / "package.json", fixtures, tmp_path / "frontend" / "package.json"):
        assert json.loads(path.read_text())["packageManager"] == "bun@1.4.4"


def test_node_modules_manifests_are_not_pins(tmp_path: Path) -> None:
    _manifest(tmp_path / "package.json", "1.4.4")
    _manifest(tmp_path / "node_modules" / "dep" / "package.json", "0.1.0")

    with (
        patch.object(remediations, "_pm_runtime_version", return_value="1.4.4"),
        patch.object(remediations, "_pm_registry_latest", return_value="1.4.4"),
    ):
        assert remediations.detect_pm_pin(_ws(tmp_path), {}) is False


def test_engines_bun_floor_is_a_minimum_not_a_pin(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"packageManager": "bun@1.4.4", "engines": {"bun": ">=1.0.0"}}) + "\n"
    )

    with (
        patch.object(remediations, "_pm_runtime_version", return_value="1.4.4"),
        patch.object(remediations, "_pm_registry_latest", return_value="1.4.4"),
    ):
        assert remediations.detect_pm_pin(_ws(tmp_path), {}) is False
    assert ">=1.0.0" in (tmp_path / "package.json").read_text()


def test_bun_upgrade_runs_once_per_sweep_and_logs_both_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _manifest(tmp_path / "package.json", "1.4.2")
    remediations._BUN_UPGRADE_DONE = False
    runtime = {"version": "1.4.2"}
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        if cmd == ["bun", "upgrade"]:
            runtime["version"] = "1.4.4"
            return _proc()
        return _proc(stdout=runtime["version"])

    try:
        with (
            patch.object(remediations, "run", side_effect=fake_run),
            patch.object(remediations, "_pm_registry_latest", return_value="1.4.4"),
        ):
            remediations.apply_pm_pin(_ws(tmp_path), {})
            # A second workspace in the same sweep must not upgrade again.
            other = tmp_path / "other"
            _manifest(other / "package.json", "1.4.2")
            remediations.apply_pm_pin(_ws(other), {})
    finally:
        remediations._BUN_UPGRADE_DONE = False

    assert [c for c in calls if c == ["bun", "upgrade"]] == [["bun", "upgrade"]]
    assert "bun upgrade: 1.4.2 → 1.4.4" in capsys.readouterr().err


def test_bun_upgrade_is_skipped_when_the_local_runtime_is_current(tmp_path: Path) -> None:
    _manifest(tmp_path / "package.json", "1.4.3")
    remediations._BUN_UPGRADE_DONE = False
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return _proc(stdout="1.4.4")

    try:
        with (
            patch.object(remediations, "run", side_effect=fake_run),
            patch.object(remediations, "_pm_registry_latest", return_value="1.4.4"),
        ):
            remediations.apply_pm_pin(_ws(tmp_path), {})
    finally:
        remediations._BUN_UPGRADE_DONE = False

    assert ["bun", "upgrade"] not in calls


def test_workflow_literal_bun_version_is_bumped_and_expressions_are_not(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    literal = wf_dir / "ci.yml"
    literal.write_text(
        "jobs:\n  test:\n    steps:\n"
        "      - uses: oven-sh/setup-bun@v2\n"
        "        with:\n"
        "          bun-version: 1.4.3\n"
        "      - run: echo hi  # bun-version: 1.4.0 stays in the comment\n"
    )
    expression = wf_dir / "release.yml"
    expression.write_text(
        "jobs:\n  build:\n    steps:\n"
        "      - uses: oven-sh/setup-bun@v2\n"
        "        with:\n"
        "          bun-version: ${{ matrix.bun }}\n"
        "      - uses: oven-sh/setup-bun@v2\n"
        "        with:\n"
        "          bun-version: latest\n"
    )
    ws = _ws(tmp_path)

    with (
        patch.object(remediations, "_pm_runtime_version", return_value="1.4.4"),
        patch.object(remediations, "_pm_registry_latest", return_value="1.4.4"),
    ):
        assert remediations.detect_bun_workflow(ws, {}) is True
        ok, detail = remediations.apply_bun_workflow(ws, {})

    assert ok is True
    assert ".github/workflows/ci.yml" in detail
    assert "bun-version: 1.4.4" in literal.read_text()
    assert "bun-version: 1.4.0" in literal.read_text()
    assert expression.read_text().count("bun-version: ${{ matrix.bun }}") == 1
    assert "bun-version: latest" in expression.read_text()
