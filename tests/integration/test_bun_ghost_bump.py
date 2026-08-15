"""Integration test: bun ghost-bump guard (issue #112).

Fixture: package.json with ``>=0.1.0 <2.0.0`` range plus an ``overrides`` entry
pinning ``is-odd`` to ``0.1.2``.  ``bun outdated`` reports Update=1.0.0 but
``bun update is-odd@1.0.0`` exits 0 while node_modules stays at 0.1.2 because
bun honours the override.  ``apply_bun`` must raise a ghost-bump RuntimeError.

Network: ~1 small package download (is-odd, no install scripts).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import IS_ODD_PIN, ghost_workspace

from repo_ops_js_bun.bun import apply_bun

pytestmark = pytest.mark.integration


def test_bun_ghost_bump_overrides_raises(bun_ghost_root: Path) -> None:
    """apply_bun raises ghost-bump error when overrides block the real upgrade."""
    installed = json.loads(
        (bun_ghost_root / "node_modules" / "is-odd" / "package.json").read_text()
    )
    assert installed["version"] == IS_ODD_PIN, "pre-condition: override held"

    ws = ghost_workspace(bun_ghost_root, manager="bun", label="ghost-test")

    with pytest.raises(RuntimeError, match="ghost bump"):
        apply_bun(ws)


def test_bun_apply_targeted_bump_releases_unheld_pin_instead_of_raising(
    bun_ghost_root: Path, monkeypatch
) -> None:
    """Targeted bump releases an unheld overrides pin and retries the upgrade."""
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "1")

    ws = ghost_workspace(
        bun_ghost_root,
        manager="bun",
        label="bun-ghost-apply-targeted-test",
    )

    behind = apply_bun(ws)

    assert not any(change.name == "is-odd" for change in behind)
    pkg_after = json.loads(
        (bun_ghost_root / "package.json").read_text(encoding="utf-8")
    )
    assert "is-odd" not in pkg_after.get("overrides", {})
    installed = json.loads(
        (bun_ghost_root / "node_modules" / "is-odd" / "package.json").read_text(
            encoding="utf-8"
        )
    )
    assert installed["version"] != IS_ODD_PIN
