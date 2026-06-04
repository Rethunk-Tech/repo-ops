"""Shared repository discovery logic (git repo walking with SKIP_DIRS)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import should_exclude
from repo_ops_core.git import git_common_dir

#: Environment variable that overrides the ``find`` subprocess timeout (seconds).
#: Accepts any value parseable as a positive float; invalid values are silently
#: ignored and the default of 60 s is used instead.
FIND_TIMEOUT_ENV = "REPO_OPS_FIND_TIMEOUT"
_DEFAULT_FIND_TIMEOUT: float = 60.0


def _resolve_find_timeout() -> float:
    """Return the find timeout from *REPO_OPS_FIND_TIMEOUT* env var, or 60 s."""
    raw = os.environ.get(FIND_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return _DEFAULT_FIND_TIMEOUT


def discover_repos(
    roots: list[str],
    *,
    follow_symlinks: bool = False,
    timeout: float | None = None,
) -> dict[str, str]:
    """Map git-common-dir -> representative working tree path.

    By default ``find`` does not follow symlinks (safer for large monorepo trees).
    Pass ``follow_symlinks=True`` (CLI ``--follow-symlinks``) to add ``-L`` so
    symlinked ``.git`` metadata dirs are not missed.
    Both sides of the dedup key are os.path.realpath-normalised so that a
    symlinked repo and its canonical path map to the same key.
    Prunes directories in SKIP_DIRS except ``.git`` (we must match repo roots).
    After printing a ``.git`` directory, prune so ``find`` does not descend into it.

    The ``find`` subprocess timeout defaults to 60 s but can be overridden in
    two ways (explicit param takes precedence over env var):

    * Pass ``timeout=<seconds>`` directly to this function.
    * Set the ``REPO_OPS_FIND_TIMEOUT`` environment variable to a positive
      number of seconds (float accepted).
    """
    _timeout: float = timeout if timeout is not None else _resolve_find_timeout()
    common_to_repo: dict[str, str] = {}
    # .git is in SKIP_DIRS for os.walk-style tools but must not be pruned here:
    # pruning .git prevents ever matching ``-name .git`` on the repo metadata dir.
    prune_names = sorted(SKIP_DIRS - {".git"})
    prune_expr: list[str] = []
    for name in prune_names:
        if prune_expr:
            prune_expr.extend(["-o", "-name", name])
        else:
            prune_expr.extend(["-name", name])
    for root in roots:
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            continue
        # Build find: prune SKIP_DIRS (not .git), match .git dirs, then prune .git
        cmd = ["find"]
        if follow_symlinks:
            cmd.append("-L")
        cmd.extend(
            [
                str(root_path),
                "(",
                *prune_expr,
                ")",
                "-prune",
                "-o",
                "(",
                "-type",
                "d",
                "-name",
                ".git",
                "-print",
                ")",
                "-prune",
            ]
        )
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"WARN: find timed out under {root_path}", file=sys.stderr)
            continue
        if proc.returncode != 0:
            if not proc.stdout.strip():
                continue
            print(
                f"WARN: find exited {proc.returncode} under {root_path} (partial results used)",
                file=sys.stderr,
            )
        for git_dir in proc.stdout.splitlines():
            if not git_dir:
                continue
            repo = os.path.realpath(os.path.dirname(git_dir))
            if should_exclude(repo):
                continue
            try:
                common = git_common_dir(repo)
            except subprocess.TimeoutExpired:
                print(f"WARN: skipped {repo}: timeout", file=sys.stderr)
                continue
            if not common:
                continue
            prev = common_to_repo.get(common)
            if prev is None or len(repo) < len(prev):
                common_to_repo[common] = repo
    return common_to_repo
