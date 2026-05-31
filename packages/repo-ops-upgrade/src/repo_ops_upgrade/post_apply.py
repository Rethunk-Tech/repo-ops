"""Post-apply change detection."""

from __future__ import annotations

import re
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_core.version_utils import normalize_version
from repo_ops_lockfiles.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops_models import Change, Workspace

from repo_ops_upgrade.registry import Checker


def collect_changes_after_apply(
    ws: Workspace,
    *,
    checker: Checker | None = None,
) -> list[Change]:
    """Lightweight post-apply change detection for remediation context."""
    repo = ws.git_repo
    rel = ws.path
    try:
        rel = ws.path.relative_to(repo)
    except ValueError:
        rel = ws.path
    rel_s = str(rel)
    lock_names = (
        list(NODE_LOCKFILES)
        + list(PY_LOCKFILES)
        + list(GO_LOCKFILES)
        + list(RUST_LOCKFILES)
    )
    proc = run(
        ["git", "diff", "--name-only", "--", *lock_names],
        repo,
        timeout=60,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        changes: list[Change] = []
        for line in proc.stdout.splitlines():
            p = line.strip()
            if not p.startswith(rel_s):
                continue
            fname = Path(p).name
            if fname == "package.json":
                proc2 = run(
                    ["git", "diff", "--", p],
                    repo,
                    timeout=60,
                )
                if proc2.returncode == 0:
                    m = re.findall(
                        r'^[+-]\s*"([^"]+)":\s*"([^"]+)"',
                        proc2.stdout,
                        re.MULTILINE,
                    )
                    for name, ver in m:
                        if name in ("dependencies", "devDependencies"):
                            continue
                        changes.append(Change(name, "?", normalize_version(ver)))
            elif fname.endswith(".lock") or fname in (
                "go.sum",
                "go.mod",
                "requirements.txt",
            ):
                changes.append(Change(fname, "changed", "updated"))
        if changes:
            return changes
    if checker:
        try:
            out = checker(ws)
            if isinstance(out, tuple):
                return out[0]
            return out
        except Exception:
            pass
    return []
