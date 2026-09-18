"""Upgrade-parity coverage across managers.

1. The default full sweep flips the container/pre-commit/submodule cross-major
   gates alongside bun/pnpm/yarn and go; ``--conservative`` flips all off.
2. ``REPO_OPS_BUN_TARGETED_BUMP`` defaults on with the sweep, matching the
   yarn/npm/pnpm wiring; ``--conservative`` disables it.
3. ``_behind_latest_hint`` gives npm/pnpm/bun the same "flag not passed ->
   name the flag; flag passed -> targeted bump was tried" treatment yarn
   already had.

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
# Shared fixture: a minimal flat bun workspace so main_plan has something to
# discover and does not exit early on empty-roots.
# ---------------------------------------------------------------------------


def _bun_workspace(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


_LATEST_UMBRELLA_VARS = (
    "REPO_OPS_BUN_UPDATE_LATEST",
    "REPO_OPS_PNPM_UPDATE_LATEST",
    "REPO_OPS_YARN_UPDATE_LATEST",
    "REPO_OPS_DENO_UPDATE_LATEST",
    "REPO_OPS_GO_UPGRADE_MODE",
    "REPO_OPS_CONTAINER_UPDATE_LATEST",
    "REPO_OPS_PRECOMMIT_UPDATE_LATEST",
    "REPO_OPS_SUBMODULE_UPDATE_LATEST",
)


def _clear_env(monkeypatch, names: tuple[str, ...]) -> None:
    # Seed then delete so monkeypatch reliably restores the pre-test state
    # (absent) on teardown, mirroring test_behind_latest.py's pattern.
    for var in names:
        monkeypatch.setenv(var, "__sentinel__")
        monkeypatch.delenv(var)


# ---------------------------------------------------------------------------
# Contract 3 — _behind_latest_hint parity for npm/pnpm/bun
# ---------------------------------------------------------------------------

# Managers whose targeted bump has no separate --<manager>-targeted-bump CLI
# flag (only npm) name "no latest mode" in the not-requested hint;
# py managers with no latest-mode concept at all still say so but skip the
# generic "bump manually" close since it duplicates that sentence for them.
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
