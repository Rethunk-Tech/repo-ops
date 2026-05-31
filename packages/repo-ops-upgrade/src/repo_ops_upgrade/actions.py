"""GitHub Actions workflow pin audit and apply."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from repo_ops_core.process import run
from repo_ops_models import ActionBump

from repo_ops_upgrade.constants import ACTION_ALLOWLIST, USES_RE


def replace_action_pin_in_text(text: str, action: str, pin: str, latest: str) -> str:
    """Replace one action pin only on matching ``uses:`` lines (never substring)."""
    parts: list[str] = []
    last = 0
    for m in USES_RE.finditer(text):
        parts.append(text[last : m.start()])
        line = m.group(0)
        if m.group(1) == action and m.group(2) == pin:
            line = line.replace(f"{action}@{pin}", f"{action}@{latest}", 1)
        parts.append(line)
        last = m.end()
    parts.append(text[last:])
    return "".join(parts)


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


def scan_actions(repo: Path, apply: bool) -> list[ActionBump]:
    if not shutil.which("actionlint"):
        print(
            "WARN: actionlint not on PATH; skipping action validation", file=sys.stderr
        )
    bumps: list[ActionBump] = []
    for wf in discover_workflow_files(repo):
        text = wf.read_text(encoding="utf-8", errors="replace")
        if shutil.which("actionlint"):
            proc = run(["actionlint", str(wf)], repo, timeout=60)
            if proc.returncode != 0:
                bumps.append(
                    ActionBump(
                        workflow=str(wf.relative_to(repo)),
                        action="(actionlint)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=proc.stderr.strip() or proc.stdout.strip(),
                    )
                )
                continue
        file_changed = False
        for m in USES_RE.finditer(text):
            action, pin = m.group(1), m.group(2)
            if pin.startswith("sha") or re.fullmatch(r"[0-9a-f]{7,40}", pin):
                bumps.append(
                    ActionBump(
                        workflow=str(wf.relative_to(repo)),
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
                bump = ActionBump(
                    workflow=str(wf.relative_to(repo)),
                    action=action,
                    pin=pin,
                    latest=latest,
                    status="ok",
                )
                bumps.append(bump)
                if apply:
                    text = replace_action_pin_in_text(text, action, pin, latest)
                    file_changed = True
        if apply and file_changed:
            wf.write_text(text, encoding="utf-8")
    return bumps
