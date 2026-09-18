"""Upgrade-parity coverage across managers.

``_behind_latest_hint`` names, per manager, why a targeted bump could not
move a package.

No subprocess or network calls are made.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_js_bun.bun import targeted_bump_behind as bun_targeted_bump_behind
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.report import _behind_latest_hint

# ---------------------------------------------------------------------------
# _behind_latest_hint parity — _behind_latest_hint parity for npm/pnpm/bun
# ---------------------------------------------------------------------------

_TRIED_FRAGMENT = "the targeted bump was attempted but could not move them"

_BEHIND_LATEST_HINT_CASES = [
    ("npm", (_TRIED_FRAGMENT, "bump manually")),
    ("pnpm", (_TRIED_FRAGMENT, "bump manually")),
    ("bun", (_TRIED_FRAGMENT, "bump manually")),
    ("deno", (_TRIED_FRAGMENT, "bump manually")),
    (
        "poetry",
        (_TRIED_FRAGMENT, "bump manually"),
    ),
    ("pdm", (_TRIED_FRAGMENT, "bump manually")),
    (
        "pipenv",
        (_TRIED_FRAGMENT, "bump manually"),
    ),
    (
        "pip-tools",
        (_TRIED_FRAGMENT, "bump manually"),
    ),
]


@pytest.mark.parametrize(
    "manager,expected_fragments",
    _BEHIND_LATEST_HINT_CASES,
    ids=[manager for manager, _ in _BEHIND_LATEST_HINT_CASES],
)
def test_behind_latest_hint(manager, expected_fragments) -> None:
    hint = _behind_latest_hint(manager)
    for fragment in expected_fragments:
        assert fragment in hint


# ---------------------------------------------------------------------------
# Regression: ghost-bump exception must not skip snapshot restore.
#
# ``targeted_bump_behind`` must wrap the ``assert_targets_resolved`` guard
# call in try/except: appending the Change to ``applied`` and then calling
# the guard unguarded would let a ghost-bump RuntimeError (discovered
# post-hoc) propagate straight out of the loop, skipping
# ``restore_workspace_snapshot`` and leaving a mutated lockfile on disk. A
# raise must restore the snapshot and record the package as still-behind
# instead.
# ---------------------------------------------------------------------------


def test_bun_targeted_bump_restores_snapshot_when_guard_raises(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"zod": "^3.25.76"}}), encoding="utf-8"
    )
    (tmp_path / "bun.lock").write_text("original-lock-content", encoding="utf-8")
    original_lock = (tmp_path / "bun.lock").read_bytes()

    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="demo",
    )

    def fake_run(cmd, cwd, timeout=600, env=None):
        # Simulate a successful install that resolved forward, then a lock
        # mutation — this is what must get reverted by the restore.
        (tmp_path / "bun.lock").write_text("mutated-lock-content", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_guard(ws, requested, manager="bun"):
        raise RuntimeError("ghost bump: bun upgrade blocked (override/pin)")

    with (
        patch("repo_ops_js_bun.bun.run", fake_run),
        patch(
            "repo_ops_js_bun.bun.resolved_version_from_node_modules",
            return_value="4.0.0",
        ),
        patch("repo_ops_js_bun.bun.assert_targets_resolved", fake_guard),
    ):
        applied, still_behind = bun_targeted_bump_behind(ws, [Change("zod", "3.25.76", "4.0.0")])

    assert applied == []
    assert still_behind == [Change("zod", "3.25.76", "4.0.0")]
    # The lockfile mutated by the fake install must be reverted, not left
    # corrupted, once the ghost-bump guard raises.
    assert (tmp_path / "bun.lock").read_bytes() == original_lock
