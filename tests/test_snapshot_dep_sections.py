"""_pkg_json_dep_changes covers all four dep sections including optionalDependencies."""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_lockfiles.snapshots import _pkg_json_dep_changes, diff_workspace_snapshots


def _enc(obj: dict) -> bytes:
    return json.dumps(obj).encode()


def test_optional_dep_upgrade_emits_change() -> None:
    before = _enc({"optionalDependencies": {"fsevents": "2.3.2"}})
    after = _enc({"optionalDependencies": {"fsevents": "2.3.3"}})
    changes = _pkg_json_dep_changes(before, after)
    names = {c.name for c in changes}
    assert "fsevents" in names  # upgrade in optionalDependencies must be captured


def test_all_four_sections_captured() -> None:
    before = _enc(
        {
            "dependencies": {"a": "1.0.0"},
            "devDependencies": {"b": "1.0.0"},
            "peerDependencies": {"c": "1.0.0"},
            "optionalDependencies": {"d": "1.0.0"},
        }
    )
    after = _enc(
        {
            "dependencies": {"a": "1.1.0"},
            "devDependencies": {"b": "1.1.0"},
            "peerDependencies": {"c": "1.1.0"},
            "optionalDependencies": {"d": "1.1.0"},
        }
    )
    changes = _pkg_json_dep_changes(before, after)
    names = {c.name for c in changes}
    assert names == {"a", "b", "c", "d"}  # all four sections yield a change entry


def test_unchanged_optional_dep_not_emitted() -> None:
    pkg = _enc({"optionalDependencies": {"fsevents": "2.3.2"}})
    changes = _pkg_json_dep_changes(pkg, pkg)
    assert not changes  # identical versions → no change entries


def test_diff_coarse_includes_deno_pdm_pipfile(tmp_path: Path) -> None:
    """Snapshot diff emits coarse Change rows for deno/pdm/Pipfile locks (F076)."""
    for name in ("deno.lock", "pdm.lock", "Pipfile.lock"):
        p = tmp_path / name
        changes = diff_workspace_snapshots({p: b"v1"}, {p: b"v2"}, "uv")
        assert any(c.name == name for c in changes)
