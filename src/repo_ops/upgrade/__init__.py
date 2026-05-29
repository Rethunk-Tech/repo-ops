"""Dependency and GitHub Actions upgrade plan/apply (repos-upgrade-*)."""

from repo_ops.upgrade.run import main_apply, main_plan, run_upgrade

__all__ = ["main_apply", "main_plan", "run_upgrade"]
