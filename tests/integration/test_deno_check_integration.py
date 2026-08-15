"""Integration test: deno outdated via check_deno.

Uses on-disk lock fixtures from ``tests/fixtures/`` plus a minimal ``deno.json``.
Runs the real ``check_deno`` helper when ``deno`` is on PATH.

Network: ``deno outdated`` may reach the JSR/npm registries; skipped on failure.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from repo_ops_js.deno import check_deno
from repo_ops_models import Workspace

pytestmark = pytest.mark.integration

_DENO = shutil.which("deno")
_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.skipif(_DENO is None, reason="deno not on PATH")
def test_check_deno_runs_outdated_against_fixture_lock(tmp_path: Path) -> None:
    """check_deno returns a list (possibly empty) for a real Deno workspace."""
    lock_src = _FIXTURE_DIR / "deno_v3.lock"
    if not lock_src.exists():
        pytest.skip("deno_v3.lock fixture missing")

    (tmp_path / "deno.lock").write_bytes(lock_src.read_bytes())
    (tmp_path / "deno.json").write_text(
        json.dumps(
            {
                "imports": {
                    "@std/assert": "jsr:@std/assert@0.221.0",
                    "chalk": "npm:chalk@5.2.0",
                }
            }
        ),
        encoding="utf-8",
    )

    ws = Workspace(
        path=tmp_path,
        ecosystem="deno",
        manager="deno",
        git_repo=tmp_path,
        label="deno-integ",
    )

    try:
        changes, behind = check_deno(ws)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if any(kw in msg for kw in ("network", "fetch", "connect", "timeout")):
            pytest.skip(f"deno outdated failed (network): {exc}")
        raise

    assert isinstance(changes, list)
    assert isinstance(behind, list)
