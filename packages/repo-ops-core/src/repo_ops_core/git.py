"""Git helpers shared by repo-ops commands."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: Environment variable that overrides the default git subprocess timeout (seconds).
#: Accepts any value parseable as a positive float; invalid values are silently
#: ignored and the built-in default of 10 s is used instead.
_GIT_TIMEOUT_ENV = "REPO_OPS_GIT_TIMEOUT"
_DEFAULT_GIT_TIMEOUT: float = 10.0


def _resolve_git_timeout() -> float:
    """Return the git timeout from *REPO_OPS_GIT_TIMEOUT* env var, or 10 s."""
    raw = os.environ.get(_GIT_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return _DEFAULT_GIT_TIMEOUT


def run_git(
    repo: Path | str, *args: str, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    repo_path = Path(repo)
    if not repo_path.is_dir():
        msg = f"git -C target is not a directory: {repo_path}"
        raise FileNotFoundError(msg)
    effective_timeout = timeout if timeout is not None else _resolve_git_timeout()
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        timeout=effective_timeout,
    )


def git_repo_for(path: Path) -> Path | None:
    cur = path.resolve()
    for parent in [cur, *cur.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def git_common_dir(repo: Path | str) -> str | None:
    repo_path = Path(repo)
    proc = run_git(repo_path, "rev-parse", "--git-common-dir")
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    common = Path(raw)
    if not common.is_absolute():
        common = repo_path / common
    return os.path.realpath(str(common))


def is_git_dirty(repo: Path | str) -> bool:
    """True when the worktree has any unstaged or uncommitted changes."""
    proc = run_git(repo, "status", "--porcelain", timeout=30)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def repo_label(repo: Path | str, roots: list[str]) -> str:
    repo_path = Path(repo).resolve()
    home = Path.home().resolve()
    for root in roots:
        root_path = Path(root).resolve()
        try:
            rel = repo_path.relative_to(root_path)
            if str(rel) == ".":
                return (
                    f"~/{repo_path.relative_to(home)}"
                    if repo_path.is_relative_to(home)
                    else repo_path.name
                )
            return str(rel)
        except ValueError:
            continue
    try:
        return f"~/{repo_path.relative_to(home)}"
    except ValueError:
        return str(repo_path)
