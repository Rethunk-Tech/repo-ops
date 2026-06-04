"""bun upgrade check/apply."""

from __future__ import annotations

import json
import os
import re

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY

# bun >= 1.3 has no `--json` for `outdated`; it always prints an ANSI-coloured
# box-drawing table led by a `bun outdated vX (hash)` banner. Parse that table
# rather than whitespace-splitting (which mis-read the banner as a package named
# "bun" at version "(hash)" and fed `bun update bun@(hash)` -> hard failure).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_BOX_V = "│"  # │ box drawings light vertical (column separator)
_DEP_GROUP_SUFFIX_RE = re.compile(r"\s*\((?:dev|peer|optional)\)\s*$")
_PKG_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$")
_BUN_UPDATE_LATEST_ENV = "REPO_OPS_BUN_UPDATE_LATEST"


def _bun_update_latest_enabled() -> bool:
    return os.environ.get(_BUN_UPDATE_LATEST_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _valid_bun_package_name(name: str) -> bool:
    n = (name or "").strip()
    # Reject only the table header (``Package``) and dash-led junk. A real npm
    # dependency literally named ``bun`` is valid: the ``bun outdated`` banner is
    # filtered by STRUCTURE (it is not a ``│``-delimited row) in
    # ``_changes_from_bun_text``, not by excluding the name ``bun`` here (#67).
    if not n or n.startswith("-") or n == "Package":
        return False
    return bool(_PKG_NAME_RE.match(n))


def _changes_from_bun_json(stdout: str) -> tuple[list[Change], list[Change]]:
    """Forward-compatible parser for a future `bun outdated --json`.

    Range-respecting by default (``update`` field); ``latest`` when opted in.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` contains
    packages that have a newer ``latest`` version available but are not
    actionable because latest-mode is off (and the in-range update equals
    the current version).
    """
    want_latest = _bun_update_latest_enabled()
    changes: list[Change] = []
    behind_latest: list[Change] = []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return changes, behind_latest
    if not isinstance(data, dict):
        return changes, behind_latest
    for name, meta in data.items():
        if not _valid_bun_package_name(str(name)) or not isinstance(meta, dict):
            continue
        cur = str(meta.get("current") or meta.get("installed") or "?")
        latest = str(meta.get("latest", "?"))
        if want_latest:
            target = latest
        else:
            target = str(meta.get("update") or meta.get("latest") or "?")
        if cur != target and target not in ("?", ""):
            changes.append(Change(str(name), cur, target))
        elif not want_latest and latest not in ("?", "") and cur != latest:
            # In-range is already current, but a newer latest exists.
            behind_latest.append(Change(str(name), cur, latest))
    return changes, behind_latest


def _changes_from_bun_text(stdout: str) -> tuple[list[Change], list[Change]]:
    """Parse ``bun outdated``'s ANSI box table.

    Columns: ``Package │ Current │ Update │ Latest``. ``Update`` is the newest
    version within the package.json range; ``Latest`` ignores the range. Only
    ``│``-delimited data rows are read, so the banner, the ``Resolving…`` line,
    the box borders, and the ``Package`` header are all skipped.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` contains
    packages that have a newer ``latest`` version available but are not
    actionable because latest-mode is off (and the in-range update equals
    the current version).
    """
    want_latest = _bun_update_latest_enabled()
    changes: list[Change] = []
    behind_latest: list[Change] = []
    for raw in stdout.splitlines():
        line = _ANSI_RE.sub("", raw).strip()
        if not line.startswith(_BOX_V):
            continue
        # "│ name │ current │ update │ latest │" -> ['', name, cur, upd, lat, '']
        cells = [c.strip() for c in line.split(_BOX_V)]
        if len(cells) < 6:
            continue
        name = _DEP_GROUP_SUFFIX_RE.sub("", cells[1]).strip()
        if name == "Package" or not _valid_bun_package_name(name):
            continue
        current = cells[2]
        update_ver = cells[3]
        latest_ver = cells[4]
        target = latest_ver if want_latest else update_ver
        if not current or not target or current == target:
            # When latest-mode is off, check if there's a newer latest to report.
            if not want_latest and latest_ver and current and current != latest_ver:
                behind_latest.append(Change(name, current, latest_ver))
            continue
        changes.append(Change(name, current, target))
    return changes, behind_latest


def check_bun(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated bun packages.

    Returns ``(changes, behind_latest)`` where ``behind_latest`` holds packages
    that have a newer ``latest`` version available but are not actioned when
    ``REPO_OPS_BUN_UPDATE_LATEST`` is unset.
    """
    proc_json = run(["bun", "outdated", "--json"], ws.path)
    if proc_json.returncode in (0, 1) and proc_json.stdout.strip():
        try:
            data = json.loads(proc_json.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return _changes_from_bun_json(proc_json.stdout)

    proc = run(["bun", "outdated"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "bun outdated failed"
        )
    return _changes_from_bun_text(proc.stdout)


def apply_bun(ws: Workspace) -> None:
    """Apply upgrades reported by ``check_bun`` without rewriting ranges to ``latest``."""
    changes, _behind = check_bun(ws)
    if not changes:
        return
    targets: list[str] = []
    for ch in changes:
        name = (ch.name or "").strip()
        version = (ch.to or "").strip()
        if not name or not version or version in ("?", "latest", "|"):
            continue
        if not _valid_bun_package_name(name):
            continue
        targets.append(f"{name}@{version}")
    if not targets:
        proc = run(["bun", "update"], ws.path, timeout=TIMEOUT_APPLY)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "bun update failed")
        return
    proc = run(["bun", "update", "--", *targets], ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "bun update failed")
