"""Integration test: npm ghost-bump guard (issue #112, npm sibling).

Exercises ``assert_targets_resolved`` against a real ``node_modules`` directory
populated by ``npm install``.  The guard is the core of the ghost-bump fix; this
test confirms it reads the real installed version from disk and raises when the
installed version is behind the requested target.

We call ``assert_targets_resolved`` directly with the Change that npm "would have"
attempted, because npm (unlike bun) has no ``overrides`` mechanism that lets
``npm update`` exit 0 while leaving node_modules behind.  Mocking the package
manager itself is explicitly disallowed (it defeats the tier's purpose); mocking
only the *reported target* while keeping the real node_modules read is the correct
integration boundary for npm.

Network: ~1 small package download (is-odd, no install scripts).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_models import Change, Workspace

pytestmark = pytest.mark.integration

_NPM = shutil.which("npm")


@pytest.mark.skipif(_NPM is None, reason="npm not on PATH")
def test_npm_guard_fires_when_node_modules_behind_target() -> None:
    """assert_targets_resolved raises ghost-bump when node_modules has old version.

    Real npm installs is-odd@0.1.2.  The guard is given a Change requesting 1.0.0.
    Since 0.1.2 < 1.0.0, it raises RuntimeError("ghost bump: npm ...").
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        pkg = {
            "name": "npm-ghost-test",
            "version": "1.0.0",
            "dependencies": {"is-odd": "0.1.2"},
        }
        (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        assert _NPM is not None  # guaranteed by skipif
        proc = subprocess.run(
            [_NPM, "install", "--registry", "https://registry.npmjs.org"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            pytest.skip(f"npm install failed (network?): {proc.stderr.strip()[:200]}")

        installed_ver = json.loads(
            (root / "node_modules" / "is-odd" / "package.json").read_text()
        )["version"]
        assert installed_ver == "0.1.2", "pre-condition: npm installed exact pin"

        ws = Workspace(
            path=root,
            ecosystem="node",
            manager="npm",
            git_repo=root,
            label="npm-ghost-test",
        )
        # Simulate what apply_npm would pass after npm outdated reported wanted=1.0.0.
        requested = [Change("is-odd", "0.1.2", "1.0.0")]

        with pytest.raises(RuntimeError, match="ghost bump"):
            assert_targets_resolved(ws, requested, manager="npm")
