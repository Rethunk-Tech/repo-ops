"""Coverage for the wave-1 upgrade-parity gaps (contracts 1-3).

1. ``--latest`` also flips the container/pre-commit/submodule cross-major
   gates (previously only bun/pnpm/yarn + go were wired).
2. ``--bun-targeted-bump`` populates ``REPO_OPS_BUN_TARGETED_BUMP`` (mirrors
   the existing yarn/npm/pnpm wiring).
3. ``_behind_latest_hint`` gives npm/pnpm/bun the same "flag not passed ->
   name the flag; flag passed -> targeted bump was tried" treatment yarn
   already had.

No subprocess or network calls are made.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import WorkspaceResult
from repo_ops_upgrade.report import _behind_latest_hint
from repo_ops_upgrade.run import main_plan

# ---------------------------------------------------------------------------
# Shared fixture: a minimal flat bun workspace so main_plan has something to
# discover and does not exit early on empty-roots.
# ---------------------------------------------------------------------------


def _init_git_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _bun_workspace(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


_LATEST_UMBRELLA_VARS = (
    "REPO_OPS_BUN_UPDATE_LATEST",
    "REPO_OPS_PNPM_UPDATE_LATEST",
    "REPO_OPS_YARN_UPDATE_LATEST",
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
# Contract 1 — --latest flips container/pre-commit/submodule gates
# ---------------------------------------------------------------------------


def test_latest_flag_sets_container_precommit_submodule_env_vars(
    tmp_path: Path, monkeypatch
) -> None:
    _clear_env(monkeypatch, _LATEST_UMBRELLA_VARS)
    _bun_workspace(tmp_path)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["container"] = os.environ.get("REPO_OPS_CONTAINER_UPDATE_LATEST")
        seen["precommit"] = os.environ.get("REPO_OPS_PRECOMMIT_UPDATE_LATEST")
        seen["submodule"] = os.environ.get("REPO_OPS_SUBMODULE_UPDATE_LATEST")
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet", "--latest"])

    assert seen == {"container": "1", "precommit": "1", "submodule": "1"}


def test_without_latest_flag_container_precommit_submodule_env_vars_unset(
    tmp_path: Path, monkeypatch
) -> None:
    _clear_env(monkeypatch, _LATEST_UMBRELLA_VARS)
    _bun_workspace(tmp_path)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["container"] = os.environ.get("REPO_OPS_CONTAINER_UPDATE_LATEST")
        seen["precommit"] = os.environ.get("REPO_OPS_PRECOMMIT_UPDATE_LATEST")
        seen["submodule"] = os.environ.get("REPO_OPS_SUBMODULE_UPDATE_LATEST")
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet"])

    assert seen == {"container": None, "precommit": None, "submodule": None}


# ---------------------------------------------------------------------------
# Contract 2 — --bun-targeted-bump wires REPO_OPS_BUN_TARGETED_BUMP
# ---------------------------------------------------------------------------


def test_bun_targeted_bump_flag_sets_env_var(tmp_path: Path, monkeypatch) -> None:
    _clear_env(
        monkeypatch,
        ("REPO_OPS_BUN_TARGETED_BUMP", "REPO_OPS_BUN_UPDATE_LATEST"),
    )
    _bun_workspace(tmp_path)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["bun_targeted_bump"] = os.environ.get("REPO_OPS_BUN_TARGETED_BUMP")
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--quiet",
                "--latest",
                "--bun-targeted-bump",
            ]
        )

    assert seen == {"bun_targeted_bump": "1"}


def test_bun_targeted_bump_flag_absent_leaves_env_var_unset(
    tmp_path: Path, monkeypatch
) -> None:
    _clear_env(monkeypatch, ("REPO_OPS_BUN_TARGETED_BUMP",))
    _bun_workspace(tmp_path)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["bun_targeted_bump"] = os.environ.get("REPO_OPS_BUN_TARGETED_BUMP")
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet"])

    assert seen == {"bun_targeted_bump": None}


# ---------------------------------------------------------------------------
# Contract 3 — _behind_latest_hint parity for npm/pnpm/bun
# ---------------------------------------------------------------------------


def test_npm_hint_names_flag_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_NPM_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("npm")
    assert hint == "no latest mode; bump the pin manually"


def test_npm_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("npm")
    assert "--npm-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_pnpm_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_PNPM_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("pnpm")
    assert hint == "run with --latest to include"


def test_pnpm_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_PNPM_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("pnpm")
    assert "--pnpm-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_bun_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_BUN_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("bun")
    assert hint == "run with --latest to include"


def test_bun_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("bun")
    assert "--bun-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_yarn_hint_unaffected_by_generalization(monkeypatch) -> None:
    """Regression guard: generalizing the yarn branch must not change its text."""
    monkeypatch.delenv("REPO_OPS_YARN_TARGETED_BUMP", raising=False)
    assert _behind_latest_hint("yarn") == (
        "peer/group-constrained; --latest can't move them — "
        "pass --yarn-targeted-bump to attempt, or bump manually"
    )
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    assert _behind_latest_hint("yarn") == (
        "peer/group-constrained; --yarn-targeted-bump was attempted but could not "
        "move them (verify failed or still constrained) — bump manually"
    )
