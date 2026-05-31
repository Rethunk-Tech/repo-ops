"""GitHub Actions workflow pin audit and apply."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_models import ActionBump

from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    WorkflowParseError,
    parse_workflow_uses,
    update_workflow_pins,
)

_TAG_CACHE: dict[str, str | None] = {}


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def action_latest_tag(action: str, *, cache: dict[str, str | None] | None = None) -> str | None:
    """Return the latest release tag for a GitHub Action via ``gh api``.

    Uses ``releases/latest``, then the newest tag. Requires ``gh`` on PATH;
    returns ``None`` when unavailable or the API call fails.
    """
    if not shutil.which("gh"):
        return None
    store = _TAG_CACHE if cache is None else cache
    if action in store:
        return store[action]
    try:
        proc = run(
            ["gh", "api", f"repos/{action}/releases/latest", "--jq", ".tag_name"],
            Path.cwd(),
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            store[action] = proc.stdout.strip()
            return store[action]
        proc2 = run(
            ["gh", "api", f"repos/{action}/tags", "--jq", ".[0].name"],
            Path.cwd(),
            timeout=30,
        )
        if proc2.returncode == 0 and proc2.stdout.strip():
            store[action] = proc2.stdout.strip()
            return store[action]
    except subprocess.TimeoutExpired:
        print(f"WARN: gh api timed out for {action}", file=sys.stderr)
    store[action] = None
    return None


def _run_actionlint(wf: Path, repo: Path) -> str | None:
    """Return a lint error string, or None when lint passes or is unavailable."""
    if not shutil.which("actionlint"):
        return None
    try:
        proc = run(["actionlint", str(wf)], repo, timeout=60)
    except subprocess.TimeoutExpired:
        return "actionlint timed out"
    if proc.returncode != 0:
        return proc.stderr.strip() or proc.stdout.strip() or "actionlint failed"
    return None


def scan_actions(repo: Path, apply: bool) -> tuple[list[ActionBump], int]:
    if not shutil.which("actionlint"):
        print(
            "WARN: actionlint not on PATH; skipping action validation", file=sys.stderr
        )
    workflows = discover_workflow_files(repo)
    bumps: list[ActionBump] = []
    tag_cache: dict[str, str | None] = {}
    for wf in workflows:
        rel = str(wf.relative_to(repo))
        lint_err = _run_actionlint(wf, repo)
        if lint_err:
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(actionlint)",
                    pin="",
                    latest="",
                    status="fail",
                    detail=lint_err,
                )
            )
            print(
                f"WARN: {rel}: actionlint failed; pin bumps skipped for this workflow",
                file=sys.stderr,
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
            latest = action_latest_tag(action, cache=tag_cache)
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
