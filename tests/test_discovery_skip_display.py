"""Rendering of the not-actionable discovery-skip section in the run footer.

These skips (manifest present but no lockfile / no package manager / outside a
git repo — e.g. a Capacitor mobile subproject or a generated bindings stub) are
informational, never failures.  They must read as benign, stay visually distinct
from the ``Failures:`` block, and never inflate the failure count.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from repo_ops_upgrade.report import print_run_footer


def _summary(*, discovery_skipped: int = 1, failure_count: int = 0) -> dict:
    return {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "actions_policy_violations": 0,
        "discovery_skipped": discovery_skipped,
        "failure_count": failure_count,
        "packages_upgraded": 0,
    }


def test_not_actionable_skips_render_as_informational() -> None:
    """Skips render under an informational header, not the old warning-ish one."""
    skips = [
        {
            "ecosystem": "node",
            "path": "citadel/mobile",
            "reason": "no packageManager field and no lockfile",
        }
    ]
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(
            summary=_summary(),
            failures=[],
            exit_code=0,
            discovery_skips=skips,
        )
    out = buf.getvalue()
    # Reframed header: clearly informational, not a "Discovery skips" warning block.
    assert "Skipped (not actionable" in out
    assert "no action needed" in out
    assert "Discovery skips" not in out
    # Entry is still listed (user wants them visible, just quiet).
    assert "citadel/mobile" in out
    assert "[node]" in out


def test_not_actionable_skips_are_separate_from_failures() -> None:
    """A real failure and a not-actionable skip render in distinct sections."""
    skips = [
        {
            "ecosystem": "node",
            "path": "app/mobile",
            "reason": "no packageManager field and no lockfile",
        }
    ]
    failures = [("citadel", "ci.yml: actionlint reported errors")]
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(
            summary=_summary(failure_count=1),
            failures=failures,
            exit_code=1,
            discovery_skips=skips,
        )
    out = buf.getvalue()
    # The skip path must not appear inside the Failures block.
    failures_idx = out.index("Failures:")
    skipped_idx = out.index("Skipped (not actionable")
    assert skipped_idx > failures_idx, "skip section should follow the Failures block"
    failures_block = out[failures_idx:skipped_idx]
    assert "app/mobile" not in failures_block
    # The machine-readable failure count reflects only the real failure.
    assert "failures: 1" in out


def test_no_skip_section_when_none() -> None:
    """No skip section renders when there are no not-actionable skips."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(
            summary=_summary(discovery_skipped=0),
            failures=[],
            exit_code=0,
            discovery_skips=[],
        )
    out = buf.getvalue()
    assert "Skipped (not actionable" not in out
