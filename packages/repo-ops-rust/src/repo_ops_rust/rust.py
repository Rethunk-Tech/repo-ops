"""Rust/Cargo upgrade check/apply."""

from __future__ import annotations

import re

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_rust(ws: Workspace) -> list[Change]:
    proc = run(
        ["cargo", "update", "--dry-run"], ws.path, timeout=resolve_timeout_check()
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update --dry-run failed")
    changes: list[Change] = []
    for line in (proc.stdout + proc.stderr).splitlines():
        m = re.search(r"Updating\s+(\S+)\s+v([\d.]+)\s+->\s+v([\d.]+)", line)
        if m:
            changes.append(Change(m.group(1), m.group(2), m.group(3)))
    return changes


def apply_rust(ws: Workspace) -> None:
    proc = run(["cargo", "update"], ws.path, timeout=resolve_timeout_apply())
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "cargo update failed")
