"""Regression tests for ``check_pnpm`` outdated parsing (#76, #70)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops_js_pnpm.pnpm import check_pnpm
from repo_ops_models import Workspace
from repo_ops_upgrade.constants import TIMEOUT_CHECK


def _ws() -> Workspace:
    return Workspace(
        path=Path("/tmp/proj"),
        ecosystem="node",
        manager="pnpm",
        git_repo=Path("/tmp/proj"),
        label="demo",
    )


def _make_run(stdout: str, returncode: int = 1):
    """Return a fake ``run`` that replays *stdout* and *returncode*."""

    def fake_run(cmd, cwd, timeout=600, env=None):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return fake_run


def test_check_pnpm_malformed_json_warns_and_returns_empty(capsys) -> None:
    """Malformed JSON output must produce a WARN on stderr and return ([], [])."""
    bad_output = "not valid json { at all }"

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(bad_output)):
        changes, behind_latest = check_pnpm(_ws())

    assert changes == []
    assert behind_latest == []
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    # The warning must include a snippet of the bad output so the failure is visible.
    assert "not valid json" in captured.err


def test_check_pnpm_malformed_json_snippet_truncated(capsys) -> None:
    """The raw-output snippet in the warning is capped at ~200 chars."""
    long_bad_output = "x" * 500

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(long_bad_output)):
        changes, behind_latest = check_pnpm(_ws())

    assert changes == []
    assert behind_latest == []
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    # Snippet in warning should not contain the full 500-char string.
    assert "x" * 500 not in captured.err


def test_check_pnpm_valid_json_no_warning(capsys) -> None:
    """Valid JSON output must parse correctly and emit no warning.

    Real ``pnpm outdated --format json`` always emits ``wanted``; this fixture
    omits it to exercise the conservative fallback. With no ``wanted`` we must
    NOT fabricate an actionable change to ``latest`` (apply only bumps in-range)
    — the package is reported as ``behind_latest`` instead, symmetric with the
    npm checker.
    """
    payload = {"react": {"current": "18.0.0", "latest": "19.0.0"}}

    with patch("repo_ops_js_pnpm.pnpm.run", _make_run(json.dumps(payload))):
        changes, behind_latest = check_pnpm(_ws())

    # No ``wanted`` field → wanted == "?" → not an in-range change; latest
    # (19.0.0) > current (18.0.0) → behind_latest (never a phantom change).
    assert changes == []
    assert len(behind_latest) == 1
    assert behind_latest[0].name == "react"
    assert behind_latest[0].from_ver == "18.0.0"
    assert behind_latest[0].to == "19.0.0"
    captured = capsys.readouterr()
    assert captured.err == ""


def test_check_pnpm_bucketing_and_timeout() -> None:
    """#76 + #70 — in-range pkg → changes; out-of-range only → behind_latest;
    ``run`` called with ``timeout=TIMEOUT_CHECK``."""
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
        changes, behind_latest = check_pnpm(_ws())

    assert captured_timeout == [TIMEOUT_CHECK]

    assert len(changes) == 1
    assert changes[0].name == "axios"
    assert changes[0].from_ver == "1.6.0"
    assert changes[0].to == "1.7.0"

    assert len(behind_latest) == 1
    assert behind_latest[0].name == "vue"
    assert behind_latest[0].from_ver == "3.0.0"
    assert behind_latest[0].to == "4.0.0"
