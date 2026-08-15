import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops._register import register_all
from repo_ops_js_yarn.yarn import _yarn_targeted_bump_enabled
from repo_ops_models import WorkspaceResult
from repo_ops_upgrade import run
from repo_ops_upgrade.run import main_plan


def _fake_body_args() -> MagicMock:
    args = MagicMock()
    args.json = False
    args.flat = False
    args.eco_filter = None
    args.skip_actions = True
    args.jobs = 1
    args.quiet = True
    args.strict_exit = False
    args.max_failures = 0
    return args


def test_magicmock_args_default_to_full_sweep(monkeypatch):
    """A bare MagicMock auto-creates a truthy `.conservative` attribute that is
    not literally True; _cli_flag's `is True` check must not mistake that mock
    for an explicit --conservative, so the run still gets the default sweep."""
    for name in run._SWEEP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    args = _fake_body_args()

    monkeypatch.setattr(run.workspace_discovery, "discover_all", lambda *a, **k: [])
    monkeypatch.setattr(run.workspace_discovery, "group_by_repo", lambda *a, **k: {})
    monkeypatch.setattr(run.report, "print_run_footer", lambda **kwargs: None)

    run._run_upgrade_body(apply=False, args=args, roots=[])

    assert all(os.environ[name] == "1" for name in run._SWEEP_ENV_VARS)


def test_conservative_flag_writes_sweep_vars_to_zero(monkeypatch):
    """--conservative writes every _SWEEP_ENV_VARS key to "0" explicitly rather
    than leaving it unset, so an ambient REPO_OPS_*=1 can't leak through."""
    for name in run._SWEEP_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    args = _fake_body_args()
    args.conservative = True

    monkeypatch.setattr(run.workspace_discovery, "discover_all", lambda *a, **k: [])
    monkeypatch.setattr(run.workspace_discovery, "group_by_repo", lambda *a, **k: {})
    monkeypatch.setattr(run.report, "print_run_footer", lambda **kwargs: None)

    run._run_upgrade_body(apply=False, args=args, roots=[])

    assert all(os.environ[name] == "0" for name in run._SWEEP_ENV_VARS)


def _bun_workspace(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path)
    (tmp_path / "package.json").write_text(
        '{"name":"root","packageManager":"bun@1.3.14"}',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")


def test_conservative_ignores_ambient_targeted_bump_env_var(
    tmp_path: Path, monkeypatch, init_git_repo
) -> None:
    """Regression: an ambient REPO_OPS_YARN_TARGETED_BUMP=1 already set in the
    operator's shell must not outrank --conservative. run.py used to leave a
    conservative _SWEEP_ENV_VARS entry unset, which let the higher-priority
    ambient value pass straight through resolve_setting; it must now force "0"
    so the real resolver reports the setting disabled regardless of ambient."""
    monkeypatch.setenv("REPO_OPS_YARN_TARGETED_BUMP", "1")
    _bun_workspace(tmp_path, init_git_repo=init_git_repo)
    register_all()

    resolved: dict[str, bool] = {}

    def _capture(wr: WorkspaceResult, **_kwargs) -> WorkspaceResult:
        resolved["yarn_targeted_bump"] = _yarn_targeted_bump_enabled()
        return wr

    with patch(
        "repo_ops_upgrade.run.orchestration.process_workspace",
        side_effect=_capture,
    ):
        main_plan(
            [str(tmp_path), "--flat", "--skip-actions", "--quiet", "--conservative"]
        )

    assert resolved == {"yarn_targeted_bump": False}
    assert os.environ["REPO_OPS_YARN_TARGETED_BUMP"] == "0"
