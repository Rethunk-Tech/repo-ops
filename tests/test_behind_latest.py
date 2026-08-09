"""Regression tests for behind-latest disposition (#22, #42).

Verifies that workspaces with newer-than-installed packages when latest-mode
is off, and Actions with a newer major available, are reported as a distinct
``behind_latest`` disposition (NOT ok / NOT fail), do not change exit code,
and appear in the footer hint.

Also covers #42: node/bun tuple-checker with empty behind_latest still triggers
``overrides_audit`` (the empty list must NOT suppress the call).
"""

from __future__ import annotations

import io
import subprocess
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


def _ws(tmp_path: Path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="bun",
        git_repo=tmp_path,
        label="demo",
    )


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


def test_workspace_result_behind_latest_field(tmp_path: Path) -> None:
    """WorkspaceResult has a behind_latest field, default empty."""
    ws = _ws(tmp_path)
    wr = WorkspaceResult(workspace=ws)
    assert wr.behind_latest == []
    assert wr.status == "ok"


def test_workspace_result_to_json_includes_behind_latest(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    wr = WorkspaceResult(
        workspace=ws,
        behind_latest=[Change("@hono/node-server", "1.19.14", "2.0.4")],
    )
    j = wr.to_json()
    assert "behind_latest" in j
    assert j["behind_latest"] == [
        {"name": "@hono/node-server", "from": "1.19.14", "to": "2.0.4"}
    ]
    # Existing fields must remain unchanged.
    assert "changes" in j
    assert "status" in j


# ---------------------------------------------------------------------------
# build_run_summary — packages_behind_latest counter
# ---------------------------------------------------------------------------


def test_build_run_summary_counts_behind_latest(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
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


def test_behind_latest_does_not_affect_failure_count(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
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
    """Same action@pin with newer major across N workflows → behind_latest == 1 (#74)."""
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
    # #102: remediation advice now points at the unified --latest flag
    # (no manager breakdown in this summary, so the default hint is used).
    assert "--latest" in out


def test_footer_per_manager_hints_npm_vs_latest(tmp_path: Path) -> None:
    """#102: each manager gets its own correct remediation hint.

    npm has no latest mode (bump manually); bun honours --latest.
    """
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
    # npm line: no latest mode, manual bump advice (NOT --latest).
    assert (
        "2 npm package(s) behind latest (no latest mode; bump the pin manually)" in out
    )
    # bun line: --latest remediation.
    assert "3 bun package(s) behind latest (run with --latest to include)" in out


def test_per_line_behind_latest_note_is_manager_aware(tmp_path: Path) -> None:
    """#131: the per-workspace behind-latest line must not hardcode

    "(latest-mode off)". yarn leftovers surface only after --latest ran, so
    that label was misleading; the note now matches the manager-aware footer
    hint and points yarn at --yarn-targeted-bump.
    """
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
    # yarn line points at the targeted-bump remedy (#111/#131).
    assert "--yarn-targeted-bump" in out
    assert "peer/group-constrained" in out
    # npm correctly reports it has no latest mode.
    assert "no latest mode; bump the pin manually" in out
    # Deno supports latest mode and its targeted fallback.
    assert "run with --latest to include" in out


def test_yarn_behind_latest_note_when_targeted_bump_already_requested(
    tmp_path: Path, monkeypatch
) -> None:
    """#131: once --yarn-targeted-bump was requested, the yarn behind-latest note

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
    assert "--yarn-targeted-bump was attempted but could not move them" in out
    # Must NOT tell the operator to pass a flag they already passed.
    assert "pass --yarn-targeted-bump to attempt" not in out


def test_targeted_bump_flags_set_env_vars(tmp_path: Path, monkeypatch) -> None:
    """#131: each --*-targeted-bump flag must populate its env var (else a no-op).

    cli.py advertises "Sets REPO_OPS_*_TARGETED_BUMP" for yarn/npm/pnpm; run.py
    must wire all three or npm/pnpm targeted-bump is a dead flag.
    """
    import os

    for var in (
        "REPO_OPS_YARN_TARGETED_BUMP",
        "REPO_OPS_NPM_TARGETED_BUMP",
        "REPO_OPS_PNPM_TARGETED_BUMP",
        "REPO_OPS_BUN_UPDATE_LATEST",
        "REPO_OPS_PNPM_UPDATE_LATEST",
        "REPO_OPS_YARN_UPDATE_LATEST",
        "REPO_OPS_GO_UPGRADE_MODE",
    ):
        monkeypatch.setenv(var, "__sentinel__")
        monkeypatch.delenv(var)
    _bun_workspace(tmp_path)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["yarn"] = os.environ.get("REPO_OPS_YARN_TARGETED_BUMP")
        seen["npm"] = os.environ.get("REPO_OPS_NPM_TARGETED_BUMP")
        seen["pnpm"] = os.environ.get("REPO_OPS_PNPM_TARGETED_BUMP")
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
                "--yarn-targeted-bump",
                "--npm-targeted-bump",
                "--pnpm-targeted-bump",
            ]
        )

    assert seen == {"yarn": "1", "npm": "1", "pnpm": "1"}


def test_latest_flag_sets_ecosystem_env_vars(tmp_path: Path, monkeypatch) -> None:
    """#102: --latest sets the per-ecosystem env vars before workers run."""
    import os

    # Seed each var so monkeypatch records the original and reliably restores it
    # on teardown — run.py writes to os.environ directly, so without a recorded
    # original the writes would leak into later checker tests.
    env_vars = (
        "REPO_OPS_BUN_UPDATE_LATEST",
        "REPO_OPS_PNPM_UPDATE_LATEST",
        "REPO_OPS_YARN_UPDATE_LATEST",
        "REPO_OPS_GO_UPGRADE_MODE",
    )
    for var in env_vars:
        monkeypatch.setenv(var, "__sentinel__")
        monkeypatch.delenv(var)
    _bun_workspace(tmp_path)
    register_all()

    seen: dict[str, str | None] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        seen["bun"] = os.environ.get("REPO_OPS_BUN_UPDATE_LATEST")
        seen["pnpm"] = os.environ.get("REPO_OPS_PNPM_UPDATE_LATEST")
        seen["yarn"] = os.environ.get("REPO_OPS_YARN_UPDATE_LATEST")
        seen["go"] = os.environ.get("REPO_OPS_GO_UPGRADE_MODE")
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan([str(tmp_path), "--flat", "--skip-actions", "--quiet", "--latest"])

    assert seen == {"bun": "1", "pnpm": "1", "yarn": "1", "go": "explicit"}


def test_latest_umbrella_flips_actions_mode(tmp_path: Path, monkeypatch) -> None:
    """#106: --latest opts Actions into latest mode; explicit flag overrides it."""
    # --latest writes REPO_OPS_*_UPDATE_LATEST to os.environ directly; record the
    # originals so monkeypatch restores them and the writes don't leak into other
    # checker tests (mirrors test_latest_flag_sets_ecosystem_env_vars).
    for var in (
        "REPO_OPS_BUN_UPDATE_LATEST",
        "REPO_OPS_PNPM_UPDATE_LATEST",
        "REPO_OPS_YARN_UPDATE_LATEST",
        "REPO_OPS_GO_UPGRADE_MODE",
    ):
        monkeypatch.setenv(var, "__sentinel__")
        monkeypatch.delenv(var)
    _bun_workspace(tmp_path)
    register_all()

    def _mode_for(argv: list[str]) -> str:
        with patch(
            "repo_ops_upgrade.run.actions.scan_actions",
            return_value=([], 0),
        ) as scan:
            main_plan([str(tmp_path), "--flat", "--quiet", *argv])
        mode: str = scan.call_args.kwargs["upgrade_mode"]
        return mode

    # --latest umbrella → Actions latest mode.
    assert _mode_for(["--latest"]) == "latest"
    # Explicit --actions-upgrade-mode overrides the umbrella.
    assert _mode_for(["--latest", "--actions-upgrade-mode", "normalize"]) == "normalize"
    # Neither → default normalize.
    assert _mode_for([]) == "normalize"


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
    # #106: footer now points operators at the apply path for cross-major bumps.
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


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)


def _bun_workspace(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


def test_behind_latest_workspace_does_not_change_plan_exit_code(
    tmp_path: Path,
) -> None:
    """A workspace with only behind_latest entries must yield exit_code == 0."""
    _bun_workspace(tmp_path)
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
# #42 — node tuple-checkers always call overrides_audit even when behind_latest
# is empty (the common case that triggered the regression)
# ---------------------------------------------------------------------------


def test_check_workspace_calls_overrides_audit_when_behind_latest_empty(
    tmp_path: Path,
) -> None:
    """Regression #42: bun checker returning ([], []) must still invoke overrides_audit."""
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
