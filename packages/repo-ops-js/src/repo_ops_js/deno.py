"""Deno upgrade check/apply."""

from __future__ import annotations

import re
import shutil
import sys

from repo_ops_core.constants import resolve_timeout_apply, resolve_timeout_check
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace


def check_deno(ws: Workspace) -> list[Change]:
    """Return pending upgrades for a Deno workspace.

    Runs ``deno outdated`` (Deno ≥ 2.x) and parses its tabular output.
    Supports both the box-drawing table format and plain-text column format::

        Package           Current  Latest
        jsr:@std/assert   0.221.0  0.226.0
        npm:chalk         5.2.0    5.3.0

    Falls back to an empty list with a WARN when ``deno`` is absent so the
    workspace degrades gracefully (report-only, no error).
    """
    if not shutil.which("deno"):
        print(f"WARN {ws.path}: deno not on PATH", file=sys.stderr)
        return []
    proc = run(
        ["deno", "outdated"],
        ws.path,
        timeout=resolve_timeout_check(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "deno outdated failed")
    changes: list[Change] = []
    # Plain text: "name  current  latest" (at least 3 whitespace-separated tokens)
    _PLAIN_RE = re.compile(r"^(\S+)\s+([\d][^\s]*)\s+([\d][^\s]*)")
    # Vertical-bar character used in Deno's box-drawing table output.
    _VBAR = "│"  # │ (BOX DRAWINGS LIGHT VERTICAL)
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _VBAR in stripped:
            # Box-drawing table row: split on │ to get cell contents.
            # Border rows (┌─┬─┐, ├─┼─┤, └─┴─┘) do not contain │ so they
            # fall through to the plain-text branch which won't match them.
            parts = [p.strip() for p in stripped.split(_VBAR) if p.strip()]
            if len(parts) < 3:
                continue
            name, cur, latest = parts[0], parts[1], parts[-1]
            if name.lower() in ("package", "name"):
                continue  # header row
            if cur and latest and cur != latest and re.match(r"[\d]", cur):
                changes.append(Change(name, cur, latest))
            continue
        m = _PLAIN_RE.match(stripped)
        if m:
            name, cur, latest = m.group(1), m.group(2), m.group(3)
            if name.lower() in ("package", "name") or cur == latest:
                continue
            changes.append(Change(name, cur, latest))
    return changes


def apply_deno(ws: Workspace) -> None:
    """Run ``deno outdated --update`` to upgrade all dependencies.

    Requires Deno ≥ 2.x; ``deno outdated --update`` rewrites ``deno.lock``
    and import maps in-place.
    """
    if not shutil.which("deno"):
        raise RuntimeError(f"deno not on PATH for {ws.path}")
    proc = run(
        ["deno", "outdated", "--update"],
        ws.path,
        timeout=resolve_timeout_apply(),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "deno outdated --update failed")
