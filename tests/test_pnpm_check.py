"""Regression tests for ``check_pnpm`` outdated parsing (#76, #70, #91)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_js_pnpm.pnpm import check_pnpm


def _make_run(stdout: str, returncode: int = 1):
    """Return a fake ``run`` that replays *stdout* and *returncode*."""

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return fake_run


def test_check_pnpm_malformed_json_warns_and_returns_empty(
    capsys, tmp_path: Path, make_workspace
) -> None:
    """Malformed JSON output must produce a WARN on stderr and return ([], [])."""
    bad_output = "not valid json { at all }"

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(bad_output)):
        changes, behind_latest = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )

    assert changes == []
    assert behind_latest == []
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    # The warning must include a snippet of the bad output so the failure is visible.
    assert "not valid json" in captured.err


def test_check_pnpm_malformed_json_snippet_truncated(
    capsys, tmp_path: Path, make_workspace
) -> None:
    """The raw-output snippet in the warning is capped at ~200 chars."""
    long_bad_output = "x" * 500

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(long_bad_output)):
        changes, behind_latest = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )

    assert changes == []
    assert behind_latest == []
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    # Snippet in warning should not contain the full 500-char string.
    assert "x" * 500 not in captured.err


def test_check_pnpm_valid_json_no_warning(
    capsys, tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """Valid JSON output must parse correctly and emit no warning.

    Real ``pnpm outdated --format json`` always emits ``wanted``; this fixture
    omits it to exercise the conservative fallback. With no ``wanted`` we must
    NOT fabricate an actionable change to ``latest`` (apply only bumps in-range)
    — the package is reported as ``behind_latest`` instead, symmetric with the
    npm checker.
    """
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "0")
    monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", "0")
    payload = {"react": {"current": "18.0.0", "latest": "19.0.0"}}

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(json.dumps(payload))):
        changes, behind_latest = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )

    # No ``wanted`` field → wanted == "?" → not an in-range change; latest
    # (19.0.0) > current (18.0.0) → behind_latest (never a phantom change).
    assert changes == []
    assert len(behind_latest) == 1
    assert behind_latest[0].name == "react"
    assert behind_latest[0].from_ver == "18.0.0"
    assert behind_latest[0].to == "19.0.0"
    captured = capsys.readouterr()
    assert captured.err == ""


def test_check_pnpm_bucketing_and_timeout(
    tmp_path: Path, monkeypatch, make_workspace
) -> None:
    """#76 + #70 — in-range pkg → changes; out-of-range only → behind_latest;
    ``run`` called with ``timeout=TIMEOUT_CHECK``."""
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "0")
    monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", "0")
    # vue: wanted == current (no in-range bump), latest > current → behind_latest.
    # axios: wanted > current (in-range bump) → changes.
    payload = {
        "vue": {"current": "3.0.0", "wanted": "3.0.0", "latest": "4.0.0"},
        "axios": {"current": "1.6.0", "wanted": "1.7.0", "latest": "1.7.0"},
    }
    captured_timeout: list[int | None] = []

    def fake_run(cmd: list[str], cwd: Path, timeout: int | None = None, env=None):
        captured_timeout.append(timeout)
        return subprocess.CompletedProcess(
            cmd, 1, stdout=json.dumps(payload), stderr=""
        )

    with patch("repo_ops_js_pnpm.pnpm.run", fake_run):
        changes, behind_latest = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )

    assert captured_timeout == [TIMEOUT_CHECK]

    assert len(changes) == 1
    assert changes[0].name == "axios"
    assert changes[0].from_ver == "1.6.0"
    assert changes[0].to == "1.7.0"

    assert len(behind_latest) == 1
    assert behind_latest[0].name == "vue"
    assert behind_latest[0].from_ver == "3.0.0"
    assert behind_latest[0].to == "4.0.0"


@pytest.mark.parametrize(
    "env_value,expect_in_changes",
    [
        ("1", True),  # REPO_OPS_PNPM_UPDATE_LATEST set → lands in changes
        ("", False),  # env var unset → stays in behind_latest
    ],
)
def test_check_pnpm_update_latest_env(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expect_in_changes: bool,
    tmp_path: Path,
    make_workspace,
) -> None:
    """#91 — with REPO_OPS_PNPM_UPDATE_LATEST set, a package at current==wanted
    but latest>current lands in *changes*; without it, stays in *behind_latest*."""
    # current == wanted (no in-range bump), latest > current.
    payload = {
        "lodash": {"current": "4.17.20", "wanted": "4.17.20", "latest": "4.17.21"}
    }

    # Targeted bump also promotes behind_latest into changes (#122) and
    # defaults on with the sweep (#136); pin it off so this test isolates
    # REPO_OPS_PNPM_UPDATE_LATEST specifically.
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "0")
    if env_value:
        monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", env_value)
    else:
        monkeypatch.setenv("REPO_OPS_PNPM_UPDATE_LATEST", "0")

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(json.dumps(payload))):
        changes, behind_latest = check_pnpm(
            make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")
        )

    if expect_in_changes:
        assert len(changes) == 1
        assert changes[0].name == "lodash"
        assert changes[0].to == "4.17.21"
        assert behind_latest == []
    else:
        assert changes == []
        assert len(behind_latest) == 1
        assert behind_latest[0].name == "lodash"
        assert behind_latest[0].to == "4.17.21"
