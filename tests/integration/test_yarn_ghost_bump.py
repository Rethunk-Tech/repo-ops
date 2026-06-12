"""Integration test: yarn ghost-bump guard via resolutions pin (issue #121).

Design
------
Yarn Berry (v4) ``resolutions`` entries block ``yarn up -R '*'`` from moving a
package beyond the pinned version.  The lock file and node_modules stay at the
old version even though the range in package.json would allow a newer one.

Unlike bun, ``apply_yarn`` does **not** call ``assert_targets_resolved``; it uses
lock-file diffing (``diff_workspace_snapshots``).  Because the lock does not
change when resolutions block the bump, ``apply_yarn`` reports no Change and the
ghost silently goes undetected.

This test exercises ``assert_targets_resolved`` directly — the same pattern as
``test_npm_ghost_bump.py`` — against a real yarn4+node_modules installation whose
``resolutions`` entry keeps ``is-odd`` at 0.1.2 while the dependency range
(``>=0.1.0 <2.0.0``) would permit 1.0.0+.  The guard reads the installed version
from disk and raises ``RuntimeError("ghost bump: ...")``.

The test also documents the current ``apply_yarn`` gap: with a resolutions pin the
apply path reports zero changes (no ghost-bump error), meaning the silent ghost
can reach a commit.  This is an open correctness gap to address in a future issue.

Yarn / corepack setup
---------------------
Requires corepack on PATH with a working ``corepack yarn --version`` probe.
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
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_models import Change, Workspace

pytestmark = pytest.mark.integration

_COREPACK = shutil.which("corepack")

# ---------------------------------------------------------------------------
# Skip condition: corepack absent or probe fails
# ---------------------------------------------------------------------------

_COREPACK_OK: bool = False
if _COREPACK is not None:
    try:
        _probe = subprocess.run(
            [_COREPACK, "yarn", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        _COREPACK_OK = _probe.returncode == 0
    except Exception:
        _COREPACK_OK = False

_SKIP_REASON = (
    "corepack not on PATH"
    if _COREPACK is None
    else "corepack yarn --version probe failed"
)


@pytest.mark.skipif(not _COREPACK_OK, reason=_SKIP_REASON)
def test_yarn_guard_fires_when_resolutions_block_upgrade() -> None:
    """assert_targets_resolved raises ghost-bump when resolutions pin keeps node_modules behind.

    Real yarn4+node-modules installs is-odd@0.1.2 (resolutions pin).
    The guard is given a Change requesting 1.0.0.
    Since 0.1.2 < 1.0.0, it raises RuntimeError("ghost bump: yarn ...").
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Yarn 4 default is PnP; node-modules linker makes node_modules available
        # so resolved_version_from_node_modules can read the installed version.
        (root / ".yarnrc.yml").write_text(
            "nodeLinker: node-modules\n", encoding="utf-8"
        )

        pkg = {
            "name": "yarn-ghost-test",
            "version": "1.0.0",
            "packageManager": "yarn@4.15.0",
            # Range allows 1.0.0 but resolutions pin keeps 0.1.2.
            "dependencies": {"is-odd": ">=0.1.0 <2.0.0"},
            "resolutions": {"is-odd": "0.1.2"},
        }
        (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        assert _COREPACK is not None  # guaranteed by skipif
        proc = subprocess.run(
            [_COREPACK, "yarn", "install"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0:
            pytest.skip(f"yarn install failed (network?): {proc.stderr.strip()[:200]}")

        # Pre-condition: resolutions held node_modules at 0.1.2
        nm_pkg = root / "node_modules" / "is-odd" / "package.json"
        if not nm_pkg.exists():
            pytest.skip("node_modules/is-odd not created (PnP fallback?)")
        installed = json.loads(nm_pkg.read_text(encoding="utf-8"))
        assert installed["version"] == "0.1.2", (
            f"pre-condition: resolutions should have held is-odd at 0.1.2, "
            f"got {installed['version']}"
        )

        ws = Workspace(
            path=root,
            ecosystem="node",
            manager="yarn",
            git_repo=root,
            label="yarn-ghost-test",
        )
        # Simulate the Change that yarn would have reported if it had bumped
        # is-odd to 1.0.0 — the guard checks node_modules to verify the move
        # actually happened.
        requested = [Change("is-odd", "0.1.2", "1.0.0")]

        with pytest.raises(RuntimeError, match="ghost bump"):
            assert_targets_resolved(ws, requested, manager="yarn")


@pytest.mark.skipif(not _COREPACK_OK, reason=_SKIP_REASON)
def test_yarn_apply_silent_no_raise_with_resolutions_pin() -> None:
    """Documents the apply_yarn gap: resolutions block produces no Change, no error.

    ``apply_yarn`` uses lock-file diffing, not node_modules reads.  When
    ``resolutions`` pins a package, ``yarn up -R '*'`` leaves the lock unchanged,
    so ``diff_workspace_snapshots`` returns zero Changes and apply returns []
    silently — no ghost-bump RuntimeError.

    This test pins the observed behaviour.  A future fix should wire
    ``assert_targets_resolved`` into ``apply_yarn`` (see repo-ops#121).
    """
    from repo_ops_js_yarn.yarn import apply_yarn

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        (root / ".yarnrc.yml").write_text(
            "nodeLinker: node-modules\n", encoding="utf-8"
        )

        pkg = {
            "name": "yarn-ghost-apply-test",
            "version": "1.0.0",
            "packageManager": "yarn@4.15.0",
            "dependencies": {"is-odd": ">=0.1.0 <2.0.0"},
            "resolutions": {"is-odd": "0.1.2"},
        }
        (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        assert _COREPACK is not None  # guaranteed by skipif
        proc = subprocess.run(
            [_COREPACK, "yarn", "install"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0:
            pytest.skip(f"yarn install failed (network?): {proc.stderr.strip()[:200]}")

        ws = Workspace(
            path=root,
            ecosystem="node",
            manager="yarn",
            git_repo=root,
            label="yarn-ghost-apply-test",
        )

        # apply_yarn does NOT call assert_targets_resolved — it uses lock-file
        # diffing.  With resolutions pinning is-odd@0.1.2, yarn up produces no
        # lock change → returns [] without raising.  This is the open gap.
        behind = apply_yarn(ws)

        # Documenting observed behaviour: no exception, no changes reported.
        # When the gap is fixed, this assertion should change to expect a
        # RuntimeError or a non-empty return.
        assert behind == [], (
            "apply_yarn returned non-empty behind list — unexpected for "
            "conservative mode without REPO_OPS_YARN_UPDATE_LATEST set"
        )
