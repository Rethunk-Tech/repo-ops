"""pnpm upgrade check/apply."""

from __future__ import annotations

import json
import os
import sys

from repo_ops_core.process import run
from repo_ops_models import Change, Workspace
from repo_ops_upgrade.constants import TIMEOUT_APPLY


def check_pnpm(ws: Workspace) -> list[Change]:
    proc = run(["pnpm", "outdated", "--format", "json"], ws.path)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "pnpm outdated failed")
    changes: list[Change] = []
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
            items = data if isinstance(data, list) else data.get("packages", data)
            if isinstance(items, dict):
                for name, meta in items.items():
                    if isinstance(meta, dict):
                        changes.append(
                            Change(
                                name,
                                str(meta.get("current", "?")),
                                str(meta.get("latest", "?")),
                            )
                        )
            elif isinstance(items, list):
                for meta in items:
                    if isinstance(meta, dict) and meta.get("name"):
                        changes.append(
                            Change(
                                str(meta["name"]),
                                str(meta.get("current", "?")),
                                str(meta.get("latest", "?")),
                            )
                        )
        except json.JSONDecodeError:
            snippet = proc.stdout[:200].strip()
            print(
                f"WARN: pnpm outdated returned non-JSON output: {snippet!r}",
                file=sys.stderr,
            )
    return changes


def apply_pnpm(ws: Workspace) -> None:
    # Match npm conservative bumps (semver ranges) unless the user opts into latest.
    cmd = ["pnpm", "update", "-r"]
    if os.environ.get("REPO_OPS_PNPM_UPDATE_LATEST", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        cmd.append("--latest")
    proc = run(cmd, ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "pnpm update failed")
    proc2 = run(["pnpm", "install"], ws.path, timeout=TIMEOUT_APPLY)
    if proc2.returncode != 0:
        raise RuntimeError(proc2.stderr.strip() or "pnpm install failed")
