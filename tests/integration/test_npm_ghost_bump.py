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

from pathlib import Path

import pytest
from conftest import IS_ODD_PIN, ghost_workspace, read_is_odd_version

from repo_ops_js.guards import assert_targets_resolved

pytestmark = pytest.mark.integration


def test_npm_guard_fires_when_node_modules_behind_target(
    npm_ghost_root: Path,
    is_odd_ghost_change,
) -> None:
    """assert_targets_resolved raises ghost-bump when node_modules has old version."""
    installed_ver = read_is_odd_version(npm_ghost_root)
    assert installed_ver == IS_ODD_PIN, "pre-condition: npm installed exact pin"

    ws = ghost_workspace(npm_ghost_root, manager="npm", label="npm-ghost-test")

    with pytest.raises(RuntimeError, match="ghost bump"):
        assert_targets_resolved(ws, is_odd_ghost_change, manager="npm")
