"""Regression tests for behind-latest disposition.

Verifies that workspaces with newer-than-installed packages when latest-mode
is off, and Actions with a newer major available, are reported as a distinct
``behind_latest`` disposition (NOT ok / NOT fail), do not change exit code,
and appear in the footer hint.

Also covers: node/bun tuple-checker with empty behind_latest still triggers
``overrides_audit`` (the empty list must NOT suppress the call).
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from repo_ops._register import register_all
from repo_ops_models import ActionBump, Change, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import registry
from repo_ops_upgrade.report import (
    build_run_summary,
    print_human_report,
    print_run_footer,
)
from repo_ops_upgrade.run import main_plan


def _bundle(
    tmp_path: Path,
    *,
    label: str = "demo",
    workspaces: list[WorkspaceResult] | None = None,
    actions: list[ActionBump] | None = None,
    workflows_scanned: int = 0,
) -> RepoBundle:
    return RepoBundle(
        git_repo=tmp_path,
        label=label,
        workspaces=workspaces or [],
        actions=actions or [],
        actions_workflows_scanned=workflows_scanned,
    )


# ---------------------------------------------------------------------------
# WorkspaceResult.behind_latest — field exists and JSON is additive
# ---------------------------------------------------------------------------


def test_workspace_result_behind_latest_field(tmp_path: Path, make_workspace) -> None:
    """WorkspaceResult has a behind_latest field, default empty."""
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    wr = WorkspaceResult(workspace=ws)
    assert wr.behind_latest == []
    assert wr.status == "ok"


def test_workspace_result_to_json_includes_behind_latest(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    wr = WorkspaceResult(
        workspace=ws,
        behind_latest=[Change("@hono/node-server", "1.19.14", "2.0.4")],
    )
    j = wr.to_json()
    assert "behind_latest" in j
    assert j["behind_latest"] == [{"name": "@hono/node-server", "from": "1.19.14", "to": "2.0.4"}]
    # Existing fields must remain unchanged.
    assert "changes" in j
    assert "status" in j


# ---------------------------------------------------------------------------
# build_run_summary — packages_behind_latest counter
# ---------------------------------------------------------------------------


def test_build_run_summary_counts_behind_latest(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    wr = WorkspaceResult(
        workspace=ws,
        status="ok",
        changes=[],
        behind_latest=[
            Change("pkg-a", "1.0.0", "2.0.0"),
            Change("pkg-b", "3.0.0", "4.0.0"),
        ],
    )
    bundles = {tmp_path: _bundle(tmp_path, workspaces=[wr])}
    summary = build_run_summary(bundles)
    assert summary["packages_behind_latest"] == 2
    # Status is still ok — no regression in existing counters.
    assert summary["workspaces_ok"] == 1
    assert summary["workspaces_fail"] == 0


def test_behind_latest_does_not_affect_failure_count(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="bun", label="demo")
    wr = WorkspaceResult(
        workspace=ws,
        status="ok",
        behind_latest=[Change("lodash", "4.17.21", "5.0.0")],
    )
    bundles = {tmp_path: _bundle(tmp_path, workspaces=[wr])}
    summary = build_run_summary(bundles)
    assert summary["failure_count"] == 0
    assert summary["packages_behind_latest"] == 1


# ---------------------------------------------------------------------------
# Actions behind_latest counter — "newer major available; not auto-applied"
# ---------------------------------------------------------------------------


def test_actions_summary_counts_newer_major_as_behind_latest(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump(
                workflow="ci.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
            ActionBump(
                workflow="ci.yml",
                action="actions/setup-node",
                pin="v3",
                latest="v4",
                status="ok",
                detail="normalize to floating major v3",
            ),
        ],
        workflows_scanned=1,
    )
    s = bundle.actions_summary()
    assert s["behind_latest"] == 1
    assert s["bumps"] == 1  # normalize bump is still actionable


def test_actions_summary_dedupes_behind_latest_across_workflows(
    tmp_path: Path,
) -> None:
    """Same action@pin with newer major across N workflows → behind_latest == 1."""
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump(
                workflow="ci.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
            ActionBump(
                workflow="release.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
            ActionBump(
                workflow="deploy.yml",
                action="actions/checkout",
                pin="v4",
                latest="v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
        ],
        workflows_scanned=3,
    )
    s = bundle.actions_summary()
    assert s["behind_latest"] == 1
    assert s["bumps"] == 0


def test_build_run_summary_includes_actions_behind_latest(tmp_path: Path) -> None:
    bundle = _bundle(
        tmp_path,
        actions=[
            ActionBump(
                "ci.yml",
                "actions/checkout",
                "v4",
                "v5",
                status="ok",
                detail="newer major v5 available; not auto-applied (report only)",
            ),
        ],
        workflows_scanned=1,
    )
    summary = build_run_summary({tmp_path: bundle})
    assert summary["actions_behind_latest"] == 1
    assert summary["failure_count"] == 0


# ---------------------------------------------------------------------------
# Footer hint — appears when behind_latest > 0
# ---------------------------------------------------------------------------


def test_footer_hint_appears_for_packages_behind_latest(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 3,
        "actions_behind_latest": 0,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "behind-latest" in out
    assert "3 package(s)" in out
    # remediation advice points at --conservative (a real flag);
    # no manager breakdown in this summary, so the default hint is used.
    assert "--conservative" in out


def test_footer_per_manager_hints_npm_vs_latest(tmp_path: Path, monkeypatch) -> None:
    """Each manager gets its own correct remediation hint.

    npm has no latest mode (bump manually); bun's targeted bump is pinned off
    here so the "not attempted" hint is exercised rather than the "tried but
    could not move them" one (the sweep default).
    """
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "0")
    monkeypatch.setenv("REPO_OPS_BUN_TARGETED_BUMP", "0")
    summary = {
        "workspaces_ok": 2,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 5,
        "packages_behind_latest_by_manager": {"npm": 2, "bun": 3},
        "actions_behind_latest": 0,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    # npm line: no latest mode, manual bump advice.
    assert "2 npm package(s) behind latest (no latest mode;" in out
    # bun line: targeted bump wasn't attempted this run.
    assert "3 bun package(s) behind latest (targeted bump not attempted" in out


def test_per_line_behind_latest_note_is_manager_aware(tmp_path: Path, monkeypatch) -> None:
    """The per-workspace behind-latest line must not hardcode

    "(latest-mode off)". yarn leftovers surface only after the targeted bump
    ran, so that label was misleading; the note now matches the
    manager-aware footer hint. Each manager's targeted bump is pinned off
    here (the sweep default) so the "not attempted" hints are
    exercised rather than the "tried but could not move them" ones.
    """
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "0")
    monkeypatch.setenv("REPO_OPS_NPM_TARGETED_BUMP", "0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "0")
    yarn_ws = Workspace(
        path=tmp_path / "y",
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="demo",
    )
    npm_ws = Workspace(
        path=tmp_path / "n",
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label="demo",
    )
    deno_ws = Workspace(
        path=tmp_path / "d",
        ecosystem="deno",
        manager="deno",
        git_repo=tmp_path,
        label="demo",
    )
    bundle = _bundle(
        tmp_path,
        workspaces=[
            WorkspaceResult(
                workspace=yarn_ws,
                behind_latest=[Change("@playwright/test", "1.60.0", "1.61.0")],
            ),
            WorkspaceResult(
                workspace=npm_ws,
                behind_latest=[Change("lodash", "4.17.21", "5.0.0")],
            ),
            WorkspaceResult(
                workspace=deno_ws,
                behind_latest=[Change("npm:chalk", "5.2.0", "5.3.0")],
            ),
        ],
    )
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=False,
        )
    out = buf.getvalue()
    # The misleading parenthetical is gone entirely.
    assert "(latest-mode off)" not in out
    # yarn line names the constraint and the real env var / flag.
    assert "REPO_OPS_YARN_TARGETED_BUMP" in out
    assert "peer/group-constrained" in out
    # npm correctly reports it has no latest mode.
    assert "no latest mode;" in out
    # deno: targeted bump wasn't attempted this run.
    assert "targeted bump not attempted this run" in out


def test_yarn_behind_latest_note_when_targeted_bump_already_requested(
    tmp_path: Path, monkeypatch
) -> None:
    """Once --yarn-targeted-bump was requested, the yarn behind-latest note

    must report the fallback was tried and could not move the leftovers, instead
    of re-suggesting the flag the operator already passed.
    """
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    yarn_ws = Workspace(
        path=tmp_path / "y",
        ecosystem="node",
        manager="yarn",
        git_repo=tmp_path,
        label="demo",
    )
    bundle = _bundle(
        tmp_path,
        workspaces=[
            WorkspaceResult(
                workspace=yarn_ws,
                behind_latest=[Change("@playwright/test", "1.60.0", "1.61.0")],
            ),
        ],
    )
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_human_report(
            {tmp_path: bundle},
            dry_run=True,
            only_outdated=False,
            top_n=10,
            include_actions=False,
        )
    out = buf.getvalue()
    assert "the targeted bump was attempted but could not move them" in out
    # Must NOT tell the operator to pass a flag they already passed.
    assert "pass --yarn-targeted-bump to attempt" not in out


def test_sweep_env_vars_default_on_and_conservative_off(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    """Every _SWEEP_ENV_VARS gate defaults on with no flag needed.

    run.py writes "0" for each on --conservative (an explicit CLI instruction
    outranks env/config), but leaves the default "on" case unwritten — each
    key's settings-table default is already "1", so a user's own env var or
    [tool.repo-ops] entry can still override it instead of being clobbered by
    a forced "1" here.
    """
    import os

    from repo_ops_core.config import (
        _SETTINGS_MAP,
        coerce_bool_setting,
        resolve_setting,
    )
    from repo_ops_upgrade.run import _SWEEP_ENV_VARS

    env_var_to_setting = {
        env_var: name for name, (env_var, _cfg_key, _default) in _SETTINGS_MAP.items()
    }

    for var in _SWEEP_ENV_VARS:
        monkeypatch.setenv(var, "__sentinel__")
        monkeypatch.delenv(var)
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    seen: dict[str, dict[str, str | None]] = {"default": {}, "conservative": {}}
    resolved_default: dict[str, bool] = {}

    def _capture(mode: str):
        def _inner(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
            for var in _SWEEP_ENV_VARS:
                seen[mode][var] = os.environ.get(var)
                if mode == "default":
                    # Captured here (mid-run), before the --conservative run
                    # below writes "0" into these same os.environ keys.
                    resolved_default[var] = coerce_bool_setting(
                        resolve_setting(env_var_to_setting[var])
                    )
            return wr

        return _inner

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture("default"),
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet"])

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture("conservative"),
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet", "--conservative"])

    assert all(v is None for v in seen["default"].values())
    assert all(resolved_default.values())
    assert all(v == "0" for v in seen["conservative"].values())


def test_default_sweep_sets_go_upgrade_mode_explicit(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    """The default sweep sets REPO_OPS_GO_UPGRADE_MODE=explicit via
    setdefault before workers run; --conservative skips that setdefault so a
    per-checkout go_upgrade_mode config still decides (see test_config_e2e.py's
    ``test_fleet_run_resolves_go_upgrade_mode_per_checkout``)."""
    import os

    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(key: str):
        def _inner(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
            seen[key] = os.environ.get("REPO_OPS_GO_UPGRADE_MODE")
            return wr

        return _inner

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture("default"),
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet"])
    monkeypatch.delenv("REPO_OPS_GO_UPGRADE_MODE", raising=False)
    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture("conservative"),
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet", "--conservative"])

    assert seen == {"default": "explicit", "conservative": None}


def test_latest_umbrella_flips_actions_mode(tmp_path: Path, monkeypatch, init_git_repo) -> None:
    """The default sweep opts Actions into latest mode; --conservative
    reverts to normalize; an explicit --actions-upgrade-mode always wins."""
    for var in (
        "REPO_OPS_BUN_UPDATE_LATEST",
        "REPO_OPS_PNPM_UPDATE_LATEST",
        "REPO_OPS_YARN_UPDATE_LATEST",
        "REPO_OPS_GO_UPGRADE_MODE",
    ):
        monkeypatch.setenv(var, "__sentinel__")
        monkeypatch.delenv(var)
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    def _mode_for(argv: list[str]) -> str:
        with patch(
            "repo_ops_upgrade.run.actions.scan_actions",
            return_value=([], 0),
        ) as scan:
            main_plan([str(tmp_path), "--flat", "--quiet", *argv])
        mode: str = scan.call_args.kwargs["upgrade_mode"]
        return mode

    # Default sweep → Actions latest mode.
    assert _mode_for([]) == "latest"
    # --conservative → normalize.
    assert _mode_for(["--conservative"]) == "normalize"
    # Explicit --actions-upgrade-mode overrides either umbrella.
    assert _mode_for(["--actions-upgrade-mode", "normalize"]) == "normalize"
    assert _mode_for(["--conservative", "--actions-upgrade-mode", "latest"]) == "latest"


def test_footer_hint_appears_for_actions_behind_latest(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 0,
        "actions_behind_latest": 2,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "behind-latest" in out
    assert "2 action(s)" in out
    # footer now points operators at the apply path for cross-major bumps.
    assert "--actions-upgrade-mode=latest" in out


def test_footer_hint_absent_when_none_behind_latest(tmp_path: Path) -> None:
    summary = {
        "workspaces_ok": 1,
        "workspaces_fail": 0,
        "workspaces_skip": 0,
        "dirty_skips": 0,
        "verify_failures": 0,
        "actions_bumps": 0,
        "actions_failures": 0,
        "discovery_skipped": 0,
        "packages_behind_latest": 0,
        "actions_behind_latest": 0,
        "failure_count": 0,
    }
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        print_run_footer(summary=summary, failures=[], exit_code=0)
    out = buf.getvalue()
    assert "behind-latest" not in out


# ---------------------------------------------------------------------------
# Exit code invariant — behind_latest must NOT change exit code to 1
# ---------------------------------------------------------------------------


def _bun_workspace(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


def test_behind_latest_workspace_does_not_change_plan_exit_code(
    tmp_path: Path,
    init_git_repo,
) -> None:
    """A workspace with only behind_latest entries must yield exit_code == 0."""
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    behind = [Change("@hono/node-server", "1.19.14", "2.0.4")]

    def _mock_check(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        # status=ok, no actionable changes, but behind_latest populated.
        wr.status = "ok"
        wr.changes = []
        wr.behind_latest = behind
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_mock_check,
    ):
        rc = main_plan(
            [
                str(tmp_path),
                "--flat",
                "--skip-actions",
                "--quiet",
            ]
        )
    assert rc == 0


# ---------------------------------------------------------------------------
# node tuple-checkers always call overrides_audit even when behind_latest
# is empty (the common case that triggered the regression)
# ---------------------------------------------------------------------------


def test_check_workspace_calls_overrides_audit_when_behind_latest_empty(
    tmp_path: Path,
) -> None:
    """Bun checker returning ([], []) must still invoke overrides_audit."""
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="demo",
    )
    finding = {"pkg": "lodash", "pinned": "4.17.21", "detail": "stale override"}

    def fake_bun_checker(_ws: Workspace):
        # Simulate a clean run: no actionable changes, no behind_latest entries.
        return [], []

    with (
        patch.dict(registry.CHECKERS, {"bun": fake_bun_checker}, clear=False),
        patch(
            "repo_ops_upgrade.registry.overrides_audit",
            return_value=[finding],
        ) as mock_audit,
    ):
        result = registry.check_workspace(ws)

    mock_audit.assert_called_once_with(tmp_path, "bun")
    assert result.overrides_audit == [finding]
    assert result.behind_latest == []
    assert result.changes == []
