"""Shared repository discovery logic (git repo walking with SKIP_DIRS)."""

from __future__ import annotations

import concurrent.futures
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
    candidates: list[str] = []
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
            candidates.append(repo)
    return _resolve_common_dirs(candidates)


def _resolve_one(repo: str) -> tuple[str, str | None]:
    """Resolve git-common-dir for *repo*; return ``(repo, common)`` or ``(repo, None)``."""
    try:
        return repo, git_common_dir(repo)
    except subprocess.TimeoutExpired:
        print(f"WARN: skipped {repo}: timeout", file=sys.stderr)
        return repo, None
    except (FileNotFoundError, OSError) as exc:
        print(f"WARN: skipped {repo}: {exc}", file=sys.stderr)
        return repo, None


def _resolve_common_dirs(repos: list[str]) -> dict[str, str]:
    """Resolve git-common-dir for each repo in parallel, preserving find order.

    Threads are used because each resolution is a short ``git rev-parse`` subprocess
    (I/O-bound, not CPU-bound) — the GIL is released for the duration of the
    subprocess call.  Results are sorted back to the original *repos* order before
    the dedup pass so first-seen-wins semantics are identical to the sequential
    implementation.
    """
    if not repos:
        return {}
    # Map original position for stable re-ordering after parallel completion.
    order: dict[str, int] = {r: i for i, r in enumerate(repos)}
    max_workers = min(16, len(repos), os.cpu_count() or 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_resolve_one, repo): repo for repo in repos}
        raw: list[tuple[int, str, str | None]] = []
        for fut in concurrent.futures.as_completed(futures):
            repo, common = fut.result()
            raw.append((order[repo], repo, common))
    # Restore original find order before dedup so first-seen wins deterministically.
    raw.sort(key=lambda t: t[0])
    common_to_repo: dict[str, str] = {}
    for _, repo, common in raw:
        if not common:
            continue
        prev = common_to_repo.get(common)
        if prev is None or len(repo) < len(prev):
            common_to_repo[common] = repo
    return common_to_repo
