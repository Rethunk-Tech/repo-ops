"""Regression tests for ``check_pnpm`` outdated parsing."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

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
