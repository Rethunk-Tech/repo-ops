"""Tests for audit's Go fix path: govulncheck remediation extraction + the
``go get`` / ``go mod tidy`` targeted applier.

All subprocess calls are mocked; no real network or file mutations.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops_audit.audit import (
    ApplyOutcome,
    RemediationItem,
    _apply_go_targeted,
    _audit_go,
    _extract_remediations_govulncheck_json,
    apply_remediations,
)

# ---------------------------------------------------------------------------
# _extract_remediations_govulncheck_json
# ---------------------------------------------------------------------------


def test_extract_remediations_govulncheck_json() -> None:
    """Extracts (module, fixed_version) pairs from finding objects; skips findings
    with no fixed_version or no resolvable trace/module; first-seen wins on a
    duplicate module (mirrors _extract_remediations_cargo_json's setdefault)."""
    findings = [
        {
            "osv": "GO-2023-1795",
            "fixed_version": "v1.9.1",
            "trace": [{"module": "golang.org/x/net", "version": "v1.8.0"}],
        },
        {"osv": "GO-2024-001", "trace": [{"module": "no-fix-version"}]},
        {"osv": "GO-2024-002", "fixed_version": "v1.0.0"},  # no trace → skipped
        {
            # duplicate module: first-seen fixed_version must win, not this one
            "fixed_version": "v9.9.9",
            "trace": [{"module": "golang.org/x/net", "version": "v1.8.0"}],
        },
    ]
    items = _extract_remediations_govulncheck_json(findings, "/ws")
    assert len(items) == 1
    assert items[0].package == "golang.org/x/net"
    assert items[0].safe_version == "v1.9.1"
    assert items[0].ecosystem == "go"
    assert items[0].workspace == "/ws"


def test_audit_go_populates_remediations() -> None:
    """_audit_go extracts remediations from a canned govulncheck -json finding line."""
    finding_line = json.dumps(
        {
            "finding": {
                "osv": "GO-2023-1795",
                "fixed_version": "v1.9.1",
                "trace": [{"module": "golang.org/x/net", "version": "v1.8.0"}],
            }
        }
    )
    with (
        patch("shutil.which", return_value="/usr/bin/govulncheck"),
        patch(
            "repo_ops_audit.audit._run",
            return_value=(1, finding_line + "\n", ""),
        ),
    ):
        result = _audit_go("/some/repo")
    assert result.status == "vulns"
    assert len(result.remediations) == 1
    assert result.remediations[0].package == "golang.org/x/net"
    assert result.remediations[0].safe_version == "v1.9.1"
    assert result.remediations[0].ecosystem == "go"


# ---------------------------------------------------------------------------
# _apply_go_targeted
# ---------------------------------------------------------------------------


def test_apply_go_targeted_calls_go_get_then_tidy_once() -> None:
    """Two items → two 'go get' calls, then a single trailing 'go mod tidy'."""
    items = [
        RemediationItem("golang.org/x/net", "v1.9.1", "go", "/ws"),
        RemediationItem("golang.org/x/text", "v0.14.0", "go", "/ws"),
    ]
    captured_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
        captured_cmds.append(list(cmd))
        return 0, "", ""

    with patch("repo_ops_audit.audit._run", side_effect=_fake_run):
        outcome = _apply_go_targeted("/ws", items)

    assert outcome.status == "ok"
    assert outcome.changes_count == 2
    cmd_strs = [" ".join(c) for c in captured_cmds]
    assert any("go get golang.org/x/net@v1.9.1" in s for s in cmd_strs), cmd_strs
    assert any("go get golang.org/x/text@v0.14.0" in s for s in cmd_strs), cmd_strs
    assert cmd_strs.count("go mod tidy") == 1, cmd_strs
    assert cmd_strs[-1] == "go mod tidy", cmd_strs  # tidy runs last


def test_apply_go_targeted_returns_fail_on_nonzero_go_get() -> None:
    items = [RemediationItem("golang.org/x/net", "v1.9.1", "go", "/ws")]

    with patch(
        "repo_ops_audit.audit._run", return_value=(1, "", "go: module not found")
    ):
        outcome = _apply_go_targeted("/ws", items)

    assert outcome.status == "fail"
    assert "golang.org/x/net" in outcome.error


def test_apply_go_targeted_returns_fail_on_nonzero_go_mod_tidy() -> None:
    items = [RemediationItem("golang.org/x/net", "v1.9.1", "go", "/ws")]

    def _fake_run(cmd: list[str], cwd: str) -> tuple[int, str, str]:
        if cmd[:2] == ["go", "get"]:
            return 0, "", ""
        return 1, "", "tidy failed"

    with patch("repo_ops_audit.audit._run", side_effect=_fake_run):
        outcome = _apply_go_targeted("/ws", items)

    assert outcome.status == "fail"
    assert "go mod tidy" in outcome.error


def test_apply_go_targeted_skips_empty_safe_version() -> None:
    """No resolvable items → no 'go get' AND no trailing 'go mod tidy'."""
    items = [RemediationItem("golang.org/x/net", "", "go", "/ws")]

    with patch("repo_ops_audit.audit._run", return_value=(0, "", "")) as mock_run:
        outcome = _apply_go_targeted("/ws", items)

    assert outcome.status == "ok"
    assert outcome.changes_count == 0
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# apply_remediations dispatch: go bypasses the registry/orchestration path
# ---------------------------------------------------------------------------


def test_apply_remediations_go_dispatches_to_apply_go_targeted(
    tmp_path: Path,
) -> None:
    """Go remediations are applied directly via _apply_go_targeted, never through
    the registry/orchestration.process_workspace path used by JS/py/rust."""
    ws = str(tmp_path)
    items = [RemediationItem("golang.org/x/net", "v1.9.1", "go", ws)]
    mock_outcome = ApplyOutcome(workspace=ws, status="ok", changes_count=1)

    with (
        patch("repo_ops_audit.audit.shutil.which", return_value="/usr/bin/go"),
        patch(
            "repo_ops_audit.audit._apply_go_targeted", return_value=mock_outcome
        ) as mock_apply_go,
        patch("repo_ops_upgrade.orchestration.process_workspace") as mock_pw,
    ):
        outcomes = apply_remediations(items)

    mock_apply_go.assert_called_once()
    call_args = mock_apply_go.call_args.args
    assert call_args[0] == ws
    assert call_args[1] == items
    mock_pw.assert_not_called()
    assert outcomes == [mock_outcome]


def test_apply_remediations_go_skips_when_go_absent(
    tmp_path: Path,
) -> None:
    """go not on PATH → outcome.status == 'skip', _apply_go_targeted never called."""
    ws = str(tmp_path)
    items = [RemediationItem("golang.org/x/net", "v1.9.1", "go", ws)]

    with (
        patch("repo_ops_audit.audit.shutil.which", return_value=None),
        patch("repo_ops_audit.audit._apply_go_targeted") as mock_apply_go,
    ):
        outcomes = apply_remediations(items)

    assert len(outcomes) == 1
    assert outcomes[0].status == "skip"
    assert "go" in outcomes[0].error.lower()
    mock_apply_go.assert_not_called()
