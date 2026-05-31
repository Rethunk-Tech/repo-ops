"""GitHub Actions workflow pin audit and apply."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_models import ActionBump

from repo_ops_upgrade.constants import ACTION_ALLOWLIST
from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    WorkflowParseError,
    parse_workflow_uses,
    update_workflow_pins,
)


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def action_latest_tag(action: str) -> str | None:
    if action in ACTION_ALLOWLIST:
        return ACTION_ALLOWLIST[action]
    if not shutil.which("gh"):
        return None
    proc = run(
        ["gh", "api", f"repos/{action}/releases/latest", "--jq", ".tag_name"],
        Path.cwd(),
        timeout=30,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    proc2 = run(
        ["gh", "api", f"repos/{action}/tags", "--jq", ".[0].name"],
        Path.cwd(),
        timeout=30,
    )
    if proc2.returncode == 0 and proc2.stdout.strip():
        return proc2.stdout.strip()
    return None


def scan_actions(repo: Path, apply: bool) -> tuple[list[ActionBump], int]:
    if not shutil.which("actionlint"):
        print(
            "WARN: actionlint not on PATH; skipping action validation", file=sys.stderr
        )
    workflows = discover_workflow_files(repo)
    bumps: list[ActionBump] = []
    for wf in workflows:
        rel = str(wf.relative_to(repo))
        if shutil.which("actionlint"):
            proc = run(["actionlint", str(wf)], repo, timeout=60)
            if proc.returncode != 0:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action="(actionlint)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=proc.stderr.strip() or proc.stdout.strip(),
                    )
                )
                continue
        try:
            pins = parse_workflow_uses(wf, workflow_rel=rel)
        except WorkflowParseError as exc:
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(yaml)",
                    pin="",
                    latest="",
                    status="fail",
                    detail=str(exc),
                )
            )
            continue
        replacements: list[PinReplacement] = []
        for uses in pins:
            action, pin = uses.action, uses.pin
            if pin.startswith("sha") or re.fullmatch(r"[0-9a-f]{7,40}", pin):
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=pin,
                        status="ok",
                        detail="SHA-pinned; report only",
                    )
                )
                continue
            latest = action_latest_tag(action)
            if not latest:
                continue
            if pin != latest:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=latest,
                        status="ok",
                    )
                )
                if apply:
                    replacements.append(
                        PinReplacement(
                            action=action,
                            pin=pin,
                            latest=latest,
                            line=uses.line or None,
                        )
                    )
        if apply and replacements:
            try:
                update_workflow_pins(wf, replacements)
            except WorkflowParseError as exc:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action="(yaml)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=str(exc),
                    )
                )
    return bumps, len(workflows)
