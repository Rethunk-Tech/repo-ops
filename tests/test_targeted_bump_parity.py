"""Coverage for the wave-1 upgrade-parity gaps (contracts 1-3).

1. The default full sweep also flips the container/pre-commit/submodule
   cross-major gates (previously only bun/pnpm/yarn + go were wired);
   ``--conservative`` flips all of them off.
2. ``REPO_OPS_BUN_TARGETED_BUMP`` defaults on with the sweep (mirrors the
   existing yarn/npm/pnpm wiring); ``--conservative`` disables it.
3. ``_behind_latest_hint`` gives npm/pnpm/bun the same "flag not passed ->
   name the flag; flag passed -> targeted bump was tried" treatment yarn
   already had.

No subprocess or network calls are made.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_core.config import coerce_bool_setting, resolve_setting
from repo_ops_js_bun.bun import targeted_bump_behind as bun_targeted_bump_behind
from repo_ops_models import Change, Workspace, WorkspaceResult
from repo_ops_upgrade.report import _behind_latest_hint
from repo_ops_upgrade.run import main_plan

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
# Contract 1 — --latest flips container/pre-commit/submodule gates
# ---------------------------------------------------------------------------


def test_default_sweep_sets_container_precommit_submodule_env_vars(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    _clear_env(monkeypatch, _LATEST_UMBRELLA_VARS)
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    ran: list[bool] = []

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        ran.append(True)
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet"])

    assert ran, "the run never reached a workspace"
    # #136: run.py no longer force-writes these to "1" — each key's
    # settings-table default is already "1", so with nothing overriding it
    # (env cleared above, no config file) every gate must still resolve on.
    for name in (
        "container_update_latest",
        "precommit_update_latest",
        "submodule_update_latest",
        "deno_update_latest",
    ):
        assert coerce_bool_setting(resolve_setting(name)) is True


def test_conservative_flag_zeroes_container_precommit_submodule_env_vars(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    """#136: --conservative writes each gate to "0" explicitly (not merely
    unset) so an ambient REPO_OPS_*=1 can't outrank it."""
    _clear_env(monkeypatch, _LATEST_UMBRELLA_VARS)
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["container"] = os.environ.get("REPO_OPS_CONTAINER_UPDATE_LATEST")
        seen["precommit"] = os.environ.get("REPO_OPS_PRECOMMIT_UPDATE_LATEST")
        seen["submodule"] = os.environ.get("REPO_OPS_SUBMODULE_UPDATE_LATEST")
        seen["deno"] = os.environ.get("REPO_OPS_DENO_UPDATE_LATEST")
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan(
            [str(tmp_path), "--flat", "--skip-actions", "--quiet", "--conservative"]
        )

    assert seen == {
        "container": "0",
        "precommit": "0",
        "submodule": "0",
        "deno": "0",
    }


# ---------------------------------------------------------------------------
# Contract 2 — --bun-targeted-bump wires REPO_OPS_BUN_TARGETED_BUMP
# ---------------------------------------------------------------------------


def test_bun_targeted_bump_defaults_on(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    _clear_env(
        monkeypatch,
        ("REPO_OPS_BUN_TARGETED_BUMP", "REPO_OPS_BUN_UPDATE_LATEST"),
    )
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    ran: list[bool] = []

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        ran.append(True)
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet"])

    assert ran, "the run never reached a workspace"
    # #136: no force-write — the settings-table default ("1") must resolve on.
    assert coerce_bool_setting(resolve_setting("bun_targeted_bump")) is True


def test_bun_targeted_bump_conservative_flag_disables_it(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    _clear_env(monkeypatch, ("REPO_OPS_BUN_TARGETED_BUMP",))
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
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
            [str(tmp_path), "--flat", "--skip-actions", "--quiet", "--conservative"]
        )

    assert seen == {"bun_targeted_bump": "0"}


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


def test_deno_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_DENO_TARGETED_BUMP", raising=False)
    assert _behind_latest_hint("deno") == "run with --latest to include"


def test_deno_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("deno")
    assert "--deno-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_poetry_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_POETRY_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("poetry")
    assert "--poetry-targeted-bump" in hint
    assert "no latest mode" in hint


def test_poetry_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_POETRY_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("poetry")
    assert "--poetry-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_pdm_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_PDM_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("pdm")
    assert "--pdm-targeted-bump" in hint
    assert "no latest mode" in hint


def test_pdm_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_PDM_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("pdm")
    assert "--pdm-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_pipenv_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_PIPENV_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("pipenv")
    assert "--pipenv-targeted-bump" in hint
    assert "no latest mode" in hint


def test_pipenv_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_PIPENV_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("pipenv")
    assert "--pipenv-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


def test_pip_tools_hint_default_when_not_requested(monkeypatch) -> None:
    monkeypatch.delenv("REPO_OPS_PIP_TOOLS_TARGETED_BUMP", raising=False)
    hint = _behind_latest_hint("pip-tools")
    assert "--pip-tools-targeted-bump" in hint
    assert "no latest mode" in hint


def test_pip_tools_hint_reports_tried_when_requested(monkeypatch) -> None:
    monkeypatch.setenv("REPO_OPS_PIP_TOOLS_TARGETED_BUMP", "1")
    hint = _behind_latest_hint("pip-tools")
    assert "--pip-tools-targeted-bump was attempted but could not move them" in hint
    assert "bump manually" in hint


# ---------------------------------------------------------------------------
# Regression: ghost-bump exception must not skip snapshot restore.
#
# ``targeted_bump_behind`` used to append the Change to ``applied`` and THEN
# call ``assert_targets_resolved`` — if the guard raised RuntimeError (a
# ghost bump discovered post-hoc), the exception propagated straight out of
# the loop, skipping ``restore_workspace_snapshot`` and leaving a mutated
# lockfile on disk. The fix wraps the guard call in try/except so a raise
# restores the snapshot and records the package as still-behind instead.
# ---------------------------------------------------------------------------


def test_bun_targeted_bump_restores_snapshot_when_guard_raises(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "1")
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
        applied, still_behind = bun_targeted_bump_behind(
            ws, [Change("zod", "3.25.76", "4.0.0")]
        )

    assert applied == []
    assert still_behind == [Change("zod", "3.25.76", "4.0.0")]
    # The lockfile mutated by the fake install must be reverted, not left
    # corrupted, once the ghost-bump guard raises.
    assert (tmp_path / "bun.lock").read_bytes() == original_lock


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
