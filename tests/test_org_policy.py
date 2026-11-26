"""Allowed-actions policy rows come from ``gate actions-allowed --json``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_upgrade.actions import _gate_policy_findings, scan_actions

_ROWS = [
    {
        "ref": "foo/bar@v1",
        "file": ".github/workflows/ci.yml",
        "line": 4,
        "level": "fail",
        "reason": "excluded",
    },
    {
        "ref": "baz/qux@v2",
        "file": ".github/workflows/ci.yml",
        "line": 5,
        "level": "warn",
        "reason": "unverifiable",
    },
]


def _gate(rows: object, returncode: int = 1) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=json.dumps(rows), stderr="")


def test_gate_policy_findings_groups_rows_by_file(tmp_path: Path) -> None:
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gate"),
        patch("repo_ops_upgrade.actions.run", return_value=_gate(_ROWS)) as mock_run,
    ):
        found = _gate_policy_findings(tmp_path)
    assert found == {".github/workflows/ci.yml": _ROWS}
    assert mock_run.call_args.args[0] == ["gate", "actions-allowed", "--json"]


def test_gate_policy_findings_without_gate_or_json(tmp_path: Path) -> None:
    with patch("repo_ops_upgrade.actions.shutil.which", return_value=None):
        assert _gate_policy_findings(tmp_path) == {}
    bad = subprocess.CompletedProcess([], 129, stdout="not json", stderr="")
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gate"),
        patch("repo_ops_upgrade.actions.run", return_value=bad),
    ):
        assert _gate_policy_findings(tmp_path) == {}


def test_scan_actions_turns_gate_rows_into_policy_and_warn_bumps(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text("jobs:\n  a:\n    steps:\n      - uses: foo/bar@v1\n      - uses: baz/qux@v2\n")
    with (
        patch(
            "repo_ops_upgrade.actions._gate_policy_findings",
            return_value={".github/workflows/ci.yml": _ROWS},
        ),
        patch("repo_ops_upgrade.actions.shutil.which", return_value=None),
        patch(
            "repo_ops_upgrade.actions.run", return_value=subprocess.CompletedProcess([], 1, "", "")
        ),
    ):
        bumps, _ = scan_actions(tmp_path, apply=False)
    rows = {(b.action, b.pin, b.status, b.detail) for b in bumps if b.status in ("policy", "warn")}
    assert ("foo/bar", "v1", "policy", "excluded") in rows
    assert ("baz/qux", "v2", "warn", "unverifiable") in rows


# ---------------------------------------------------------------------------
# scan_actions: policy rows are report-only (do not change exit code)
#   and do not interfere with normal pin bumps
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RepoBundle.actions_summary: policy_violations count
# ---------------------------------------------------------------------------


def test_actions_summary_policy_violations_counted() -> None:
    """actions_summary() includes policy_violations key for policy-status rows."""
    from repo_ops_models import ActionBump, RepoBundle

    bundle = RepoBundle(git_repo=Path("/fake"), label="fake")
    bundle.actions = [
        ActionBump(
            workflow="ci.yml",
            action="softprops/action-gh-release",
            pin="v1",
            latest="v1",
            status="policy",
            detail="org policy (local_only): not allowed",
        ),
        ActionBump(
            workflow="ci.yml",
            action="actions/checkout",
            pin="v3",
            latest="v4",
            status="ok",
            detail="normalize to floating major v3",
        ),
    ]
    bundle.actions_workflows_scanned = 1
    summary = bundle.actions_summary()
    assert summary["policy_violations"] == 1
    # v3 → v4 with no "report only" detail → counted as a bump
    assert summary["bumps"] == 1


@pytest.mark.parametrize("status", ["policy", "policy-blocked"])
def test_actions_summary_policy_statuses_are_not_bumps(status: str) -> None:
    """Policy-status rows count as violations, never actionable bumps."""
    from repo_ops_models import ActionBump, RepoBundle

    bundle = RepoBundle(git_repo=Path("/fake"), label="fake")
    bundle.actions = [
        ActionBump(
            workflow="ci.yml",
            action="myaction/foo",
            pin="v1",
            latest="v2",
            status=status,
            detail="org policy violation",
        ),
    ]

    summary = bundle.actions_summary()
    assert summary["policy_violations"] == 1
    assert summary["bumps"] == 0
    assert summary["failures"] == 0


def test_actionlint_warn_rows_are_not_policy_violations() -> None:
    from repo_ops_models import ActionBump, RepoBundle

    bundle = RepoBundle(git_repo=Path("/fake"), label="fake")
    bundle.actions = [ActionBump("ci.yml", "(actionlint)", "", "", status="warn", detail="too old")]

    summary = bundle.actions_summary()
    assert (summary["policy_violations"], summary["lint_warnings"], summary["bumps"]) == (0, 1, 0)
