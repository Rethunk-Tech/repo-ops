"""Integration test: pnpm ghost-bump guard.

Exercises ``assert_targets_resolved`` against a real ``node_modules`` tree
populated by ``pnpm install``.  pnpm v9+ reads override pins from
``pnpm-workspace.yaml``; the root ``package.json`` still carries
``pnpm.overrides`` for repo-ops audit paths.

We call ``assert_targets_resolved`` directly (same integration boundary as
``test_npm_ghost_bump.py``) because pnpm outdated with an active override reports
``wanted == current``, so ``apply_pnpm`` does not plan a bump to exercise end-to-end.
The unit tier in ``tests/test_pnpm_apply.py`` covers the apply guard wiring with
controlled check output.

Network: ~1 small package download (is-odd, no install scripts).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_ops_js.guards import assert_targets_resolved

from conftest import IS_ODD_PIN, ghost_workspace, read_is_odd_version

pytestmark = pytest.mark.integration


def test_pnpm_guard_fires_when_node_modules_behind_target(
    pnpm_ghost_root: Path,
    is_odd_ghost_change,
) -> None:
    """assert_targets_resolved raises ghost-bump when node_modules has old version."""
    installed_ver = read_is_odd_version(pnpm_ghost_root)
    assert installed_ver == IS_ODD_PIN, "pre-condition: pnpm installed exact pin"

    ws = ghost_workspace(pnpm_ghost_root, manager="pnpm", label="pnpm-ghost-test")

    with pytest.raises(RuntimeError, match="ghost bump"):
        assert_targets_resolved(ws, is_odd_ghost_change, manager="pnpm")
