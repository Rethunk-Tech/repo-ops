"""Run a repo-declared command after a bump so regenerated files exist before verify."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from repo_ops_core.config import load_repo_config
from repo_ops_core.constants import TIMEOUT_VERIFY
from repo_ops_core.process import run
from repo_ops_upgrade.post_apply import WorktreeState, capture_worktree_state, tree_diff

_OUTPUT_TAIL_LINES = 20


def run_generate(repo: Path) -> tuple[str | None, set[str]]:
    """Run ``[generate].run`` in *repo*.

    Returns ``(verify_error, repo-relative paths the command changed)``.
    Absent or empty ``run`` is a no-op. A non-zero exit, timeout, or spawn
    failure is a verify error carrying the output tail; it is never ignored.
    """
    table = load_repo_config(repo).get("generate")
    raw = table.get("run") if isinstance(table, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None, set()
    argv = shlex.split(raw)
    if not argv:
        return None, set()
    before = capture_worktree_state(repo)
    try:
        # Same budget as verify: a generate failure is a verify failure.
        proc = run(argv, repo, timeout=TIMEOUT_VERIFY)
    except subprocess.TimeoutExpired as exc:
        n = int(exc.timeout) if exc.timeout is not None else TIMEOUT_VERIFY
        return f"generate timed out after {n}s", _changed(repo, before)
    except OSError as exc:
        return f"generate failed: {exc}", _changed(repo, before)
    changed = _changed(repo, before)
    if proc.returncode == 0:
        return None, changed
    combined = f"{proc.stdout}\n{proc.stderr}".strip()
    tail = "\n".join(combined.splitlines()[-_OUTPUT_TAIL_LINES:])
    detail = f"{' '.join(argv)} exited {proc.returncode}"
    if tail:
        detail = f"{detail}\n{tail}"
    return detail, changed


def _changed(repo: Path, before: WorktreeState | None) -> set[str]:
    if before is None:
        return set()
    diff = tree_diff(repo, before)
    return set(diff) if diff else set()
