"""Go module upgrade check/apply."""

from __future__ import annotations

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import GO_UPGRADE_RE, TIMEOUT_APPLY


def check_go(ws: Workspace) -> list[Change]:
    proc = run(["go", "list", "-u", "-m", "all"], ws.path)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go list failed")
    changes: list[Change] = []
    for line in proc.stdout.splitlines():
        m = GO_UPGRADE_RE.search(line)
        if m:
            parts = line.split()
            name = parts[0]
            cur = parts[1] if len(parts) > 1 and not parts[1].startswith("[") else "?"
            changes.append(Change(name, cur, m.group(1)))
    return changes


def apply_go(ws: Workspace) -> None:
    for args in (["go", "get", "-u", "./..."], ["go", "get", "-t", "-u", "./..."]):
        proc = run(args, ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"{' '.join(args)} failed")
    proc = run(["go", "mod", "tidy"], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "go mod tidy failed")
