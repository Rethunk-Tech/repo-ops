"""Shared repository discovery logic (git repo walking with SKIP_DIRS)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import should_exclude
from repo_ops_core.git import git_common_dir


def discover_repos(roots: list[str]) -> dict[str, str]:
    """Map git-common-dir -> representative working tree path.

    -L tells find to follow symlinks so that symlinked .git dirs are not missed.
    Both sides of the dedup key are os.path.realpath-normalised so that a
    symlinked repo and its canonical path map to the same key.
    Prunes directories in SKIP_DIRS except ``.git`` (we must match repo roots).
    After printing a ``.git`` directory, prune so ``find`` does not descend into it.
    """
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
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        # Build find: prune SKIP_DIRS (not .git), match .git dirs, then prune .git
        cmd = [
            "find",
            "-L",
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
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            continue
        for git_dir in proc.stdout.splitlines():
            if not git_dir:
                continue
            repo = os.path.realpath(os.path.dirname(git_dir))
            if should_exclude(repo):
                continue
            common = git_common_dir(repo)
            if not common:
                continue
            prev = common_to_repo.get(common)
            if prev is None or len(repo) < len(prev):
                common_to_repo[common] = repo
    return common_to_repo
