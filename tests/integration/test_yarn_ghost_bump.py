"""Integration test: yarn ghost-bump guard via resolutions pin (issue #121).

Design
------
Yarn Berry (v4) ``resolutions`` entries block ``yarn up -R '*'`` from moving a
package beyond the pinned version.  The lock file and node_modules stay at the
old version even though the range in package.json would allow a newer one.

``test_yarn_guard_fires_when_resolutions_block_upgrade`` exercises
``assert_targets_resolved`` directly — the same pattern as
``test_npm_ghost_bump.py`` — against a real yarn4+node_modules installation whose
``resolutions`` entry keeps ``is-odd`` at 0.1.2 while the dependency range
(``>=0.1.0 <2.0.0``) would permit 1.0.0+.

``test_yarn_apply_raises_ghost_bump_with_resolutions_pin`` asserts ``apply_yarn``
raises ``RuntimeError("ghost bump: …")`` when resolutions block an in-range
upgrade (guard wired in ``repo_ops_js_yarn.yarn.apply_yarn``).

Yarn / corepack setup
---------------------
Yarn fixtures skip unless ``conftest`` module-level ``corepack yarn --version``
probe succeeds (30 s cap at import).  CI enables corepack in the integration job.
Uses ``nodeLinker: node-modules`` in ``.yarnrc.yml`` so ``node_modules`` is
populated and ``resolved_version_from_node_modules`` can read the installed
package.  Without that linker setting, Yarn 4 defaults to PnP (no node_modules)
and the guard is structurally unable to read the version from disk.

Network
-------
~4 small packages (is-odd + deps: is-number, kind-of, is-buffer).  Test is
skipped if the network install fails, matching the bun/npm pattern.

Runtime: typically <30 s (corepack activates from cache after first run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_js_yarn.yarn import apply_yarn

from conftest import IS_ODD_PIN, ghost_workspace

pytestmark = pytest.mark.integration


def test_yarn_guard_fires_when_resolutions_block_upgrade(
    yarn_ghost_root: Path,
    is_odd_ghost_change,
) -> None:
    """assert_targets_resolved raises ghost-bump when resolutions pin keeps node_modules behind."""
    installed = json.loads(
        (yarn_ghost_root / "node_modules" / "is-odd" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert installed["version"] == IS_ODD_PIN, (
        f"pre-condition: resolutions should have held is-odd at {IS_ODD_PIN}, "
        f"got {installed['version']}"
    )

    ws = ghost_workspace(yarn_ghost_root, manager="yarn", label="yarn-ghost-test")

    with pytest.raises(RuntimeError, match="ghost bump"):
        assert_targets_resolved(ws, is_odd_ghost_change, manager="yarn")


def test_yarn_apply_raises_ghost_bump_with_resolutions_pin(
    yarn_ghost_apply_root: Path,
) -> None:
    """apply_yarn raises ghost-bump when resolutions block an in-range upgrade (#121)."""
    ws = ghost_workspace(
        yarn_ghost_apply_root, manager="yarn", label="yarn-ghost-apply-test"
    )

    with pytest.raises(RuntimeError, match="ghost bump"):
        apply_yarn(ws)
