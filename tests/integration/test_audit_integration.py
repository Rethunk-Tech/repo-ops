"""Integration test: govulncheck audit against a real (minimal) Go module.

Constructs a temporary Go module that depends on a known-old version of a
standard library dependency.  Runs the real ``repo-ops audit`` check function
against it and asserts:
  - The result is a parseable WorkspaceResult (no crash).
  - counts.total_with_unknown >= 0 (i.e. the field exists and is a non-negative int).
  - The status is one of the expected values ("ok" / "vulns" / "skipped" / "error").

This is a **resilience** test, not a vuln-count assertion — the exact number of
findings changes as the vulnerability DB evolves.

Network: govulncheck may fetch the vuln DB on the first run; subsequent runs use
the local cache (usually ~/.govulncheck or $GOVULNCHECK_CACHE).  The test is
skipped on network failure so it never blocks CI in offline environments.

Prerequisites:
  - ``govulncheck`` on PATH
  - ``go`` on PATH (to create a valid module for govulncheck to scan)

Timeouts:
  - ``go mod download`` in this test: 60 s subprocess cap (skip on expiry).
  - ``_audit_go`` / govulncheck: 60 s via ``_GOVULNCHECK_TIMEOUT`` in audit.py.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from repo_ops_audit.audit import _audit_go

pytestmark = pytest.mark.integration

_GOVULNCHECK = shutil.which("govulncheck")
_GO = shutil.which("go")


@pytest.mark.skipif(
    _GOVULNCHECK is None or _GO is None,
    reason="govulncheck and go must both be on PATH",
)
def test_govulncheck_produces_parseable_result(tmp_path: Path) -> None:
    """Run _audit_go against a minimal real Go module; assert parseable result."""
    # Build a minimal go.mod + go.sum in tmp_path so govulncheck can scan it.
    # We use a well-known old version of golang.org/x/text that has historical
    # CVEs, but we do NOT assert a specific vuln count because the DB changes.
    go_mod = tmp_path / "go.mod"
    go_mod.write_text(
        textwrap.dedent("""\
            module example.com/auditintegtest

            go 1.21

            require golang.org/x/text v0.3.0

            require golang.org/x/tools v0.0.0-20180917221912-90fa682c2a6e // indirect
        """),
        encoding="utf-8",
    )

    # Write a minimal .go source file that imports the dependency so govulncheck
    # actually scans it (govulncheck only reports reachable vulns by default).
    src_dir = tmp_path
    main_go = src_dir / "main.go"
    main_go.write_text(
        textwrap.dedent("""\
            package main

            import (
                _ "golang.org/x/text/encoding"
            )

            func main() {}
        """),
        encoding="utf-8",
    )

    # Run `go mod download` to populate go.sum so govulncheck can proceed.
    try:
        proc = subprocess.run(
            [str(_GO), "mod", "download"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("go mod download timed out — network unavailable")

    if proc.returncode != 0:
        combined = (proc.stdout + proc.stderr).lower()
        if any(kw in combined for kw in ("dial", "connect", "network", "timeout", "i/o")):
            pytest.skip(f"go mod download failed (network): {proc.stderr[:200]}")
        # If it failed for a non-network reason, fail the test with the message
        pytest.fail(f"go mod download failed: {proc.stderr[:400]}")

    # Now run the actual audit function under test.
    result = _audit_go(str(tmp_path))

    # Assertions — resilience checks only, not specific vuln counts.
    assert result.status in ("ok", "vulns", "skipped", "error"), (
        f"unexpected status: {result.status!r}"
    )
    assert result.counts.total_with_unknown >= 0
    assert isinstance(result.counts.unknown, int)
    assert isinstance(result.counts.critical, int)
    assert isinstance(result.counts.high, int)
    assert isinstance(result.counts.moderate, int)
    assert isinstance(result.counts.low, int)

    # govulncheck should classify findings as unknown (no per-finding severity)
    if result.status == "vulns":
        # All govulncheck findings go into unknown bucket
        assert result.counts.unknown >= 0
        assert result.counts.high == 0, "govulncheck findings must not land in 'high' bucket"
        assert result.counts.critical == 0, (
            "govulncheck findings must not land in 'critical' bucket"
        )
