"""pnpm upgrade check/apply."""

from __future__ import annotations

import json
import os
import sys

from repo_ops_core.process import run
from repo_ops_js.guards import assert_targets_resolved
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY, TIMEOUT_CHECK

_PNPM_UPDATE_LATEST_ENV = "REPO_OPS_PNPM_UPDATE_LATEST"


def _pnpm_update_latest_enabled() -> bool:
    return os.environ.get(_PNPM_UPDATE_LATEST_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _bucket_pnpm_pkg(
    name: str,
    cur: str,
    wanted: str,
    latest: str,
    changes: list[Change],
    behind_latest: list[Change],
) -> None:
    """Bucket a single pnpm package into changes or behind_latest.

    ``wanted`` is the newest version that satisfies the declared range;
    ``latest`` is the registry's latest release (may break the range).

    When ``REPO_OPS_PNPM_UPDATE_LATEST`` is set, ``latest`` is used as the
    target so packages that are only behind latest are treated as actionable
    (matching what ``apply_pnpm`` does when it passes ``--latest``).
    """
    if _pnpm_update_latest_enabled():  # noqa: SIM102
        # Treat latest as the upgrade target — mirrors apply_pnpm --latest.
        if latest not in ("?", "") and cur != latest:
            changes.append(Change(name, cur, latest))
            return
        # latest unknown ('?') — fall through to wanted check so a known
        # in-range bump is not silently omitted (#5).
    if cur != wanted and wanted not in ("?", ""):
        changes.append(Change(name, cur, wanted))
    elif latest not in ("?", "") and cur != latest:
        behind_latest.append(Change(name, cur, latest))


def check_pnpm(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Check for outdated pnpm packages.

    Returns ``(changes, behind_latest)`` where ``changes`` contains packages
    with in-range upgrades and ``behind_latest`` holds packages whose
    ``latest`` exceeds ``current`` but the in-range ``wanted`` equals
    ``current`` (only a range-breaking bump is available).
    """
    proc = run(["pnpm", "outdated", "--format", "json"], ws.path, timeout=TIMEOUT_CHECK)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "pnpm outdated failed")
    changes: list[Change] = []
    behind_latest: list[Change] = []
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
            items = data if isinstance(data, list) else data.get("packages", data)
            if isinstance(items, dict):
                for name, meta in items.items():
                    if isinstance(meta, dict):
                        cur = str(meta.get("current", "?"))
                        wanted = str(meta.get("wanted", "?"))
                        latest = str(meta.get("latest", "?"))
                        _bucket_pnpm_pkg(
                            name, cur, wanted, latest, changes, behind_latest
                        )
            elif isinstance(items, list):
                for meta in items:
                    if isinstance(meta, dict) and meta.get("name"):
                        cur = str(meta.get("current", "?"))
                        wanted = str(meta.get("wanted", "?"))
                        latest = str(meta.get("latest", "?"))
                        _bucket_pnpm_pkg(
                            str(meta["name"]),
                            cur,
                            wanted,
                            latest,
                            changes,
                            behind_latest,
                        )
        except json.JSONDecodeError:
            snippet = proc.stdout[:200].strip()
            print(
                f"WARN: pnpm outdated returned non-JSON output: {snippet!r}",
                file=sys.stderr,
            )
    return changes, behind_latest


def apply_pnpm(ws: Workspace) -> None:
    # Match npm conservative bumps (semver ranges) unless the user opts into latest.
    changes, _behind = check_pnpm(ws)
    cmd = ["pnpm", "update", "-r"]
    if _pnpm_update_latest_enabled():
        cmd.append("--latest")
    proc = run(cmd, ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pnpm update failed")
    proc2 = run(["pnpm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "pnpm install failed")
    if changes:
        assert_targets_resolved(ws, changes, manager="pnpm")
