"""Integration test: bun ghost-bump guard (issue #112).

Fixture: package.json with ``>=0.1.0 <2.0.0`` range plus an ``overrides`` entry
pinning ``is-odd`` to ``0.1.2``.  ``bun outdated`` reports Update=1.0.0 but
``bun update is-odd@1.0.0`` exits 0 while node_modules stays at 0.1.2 because
bun honours the override.  ``apply_bun`` must raise a ghost-bump RuntimeError.

Network: ~1 small package download (is-odd, no install scripts).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from repo_ops_js_bun.bun import apply_bun
from repo_ops_models import Workspace

pytestmark = pytest.mark.integration

_BUN = shutil.which("bun")


@pytest.mark.skipif(_BUN is None, reason="bun not on PATH")
def test_bun_ghost_bump_overrides_raises() -> None:
    """apply_bun raises ghost-bump error when overrides block the real upgrade."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Range >=0.1.0 <2.0.0 makes bun outdated report Update=1.0.0.
        # overrides pin pins is-odd to 0.1.2 so node_modules never moves.
        pkg = {
            "name": "ghost-test",
            "version": "1.0.0",
            "dependencies": {"is-odd": ">=0.1.0 <2.0.0"},
            "overrides": {"is-odd": "0.1.2"},
        }
        (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        # Pre-install with the override in place so node_modules has 0.1.2.
        assert _BUN is not None  # guaranteed by skipif
        proc = subprocess.run(
            [_BUN, "install", "--registry", "https://registry.npmjs.org"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            pytest.skip(f"bun install failed (network?): {proc.stderr.strip()[:200]}")

        installed = json.loads(
            (root / "node_modules" / "is-odd" / "package.json").read_text()
        )
        assert installed["version"] == "0.1.2", "pre-condition: override held"

        ws = Workspace(
            path=root,
            ecosystem="node",
            manager="bun",
            git_repo=root,
            label="ghost-test",
        )

        # apply_bun: check_bun sees Update=1.0.0, runs bun update is-odd@1.0.0
        # (exit 0), then assert_targets_resolved reads node_modules → 0.1.2 < 1.0.0
        # → raises RuntimeError.
        with pytest.raises(RuntimeError, match="ghost bump"):
            apply_bun(ws)
