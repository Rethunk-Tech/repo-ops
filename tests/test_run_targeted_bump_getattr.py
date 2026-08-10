import argparse
import os
from unittest.mock import MagicMock

from repo_ops_upgrade import run


def test_magicmock_args_do_not_enable_targeted_bumps(monkeypatch):
    targeted_env = (
        "REPO_OPS_YARN_TARGETED_BUMP",
        "REPO_OPS_NPM_TARGETED_BUMP",
        "REPO_OPS_PNPM_TARGETED_BUMP",
        "REPO_OPS_BUN_TARGETED_BUMP",
        "REPO_OPS_DENO_TARGETED_BUMP",
    )
    for name in targeted_env:
        monkeypatch.delenv(name, raising=False)

    args = MagicMock(spec=argparse.Namespace)
    args.full = False
    args.latest = False
    args.json = False
    args.flat = False
    args.eco_filter = None
    args.skip_actions = True
    args.jobs = 1
    args.quiet = True
    args.strict_exit = False
    args.max_failures = 0

    monkeypatch.setattr(run.workspace_discovery, "discover_all", lambda *a, **k: [])
    monkeypatch.setattr(run.workspace_discovery, "group_by_repo", lambda *a, **k: {})
    monkeypatch.setattr(run.report, "print_run_footer", lambda **kwargs: None)

    run._run_upgrade_body(apply=False, args=args, roots=[])

    assert all(name not in os.environ for name in targeted_env)
