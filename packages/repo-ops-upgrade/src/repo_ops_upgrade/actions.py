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

_TAG_CACHE: dict[str, dict | None] = {}

_MAJOR_TAG_RE = re.compile(r"^v(\d+)(?:\.|$)")


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def action_tag_info(action: str, *, cache: dict[str, dict | None] | None = None) -> dict | None:
    """Return tag info dict for a GitHub Action via ``gh api repos/<action>/tags``.

    Returns a dict with:
      - ``bare_majors``: set of integer majors that have a bare ``vN`` tag.
      - ``max_major``: maximum integer major across all version tags, or None.

    Returns None when ``gh`` is absent or the API call fails (caller should skip
    the pin — mirrors old graceful degradation).
    """
    if not shutil.which("gh"):
        return None
    store = _TAG_CACHE if cache is None else cache
    if action in store:
        return store[action]
    try:
        proc = run(
            ["gh", "api", f"repos/{action}/tags", "--paginate", "--jq", ".[].name"],
            Path.cwd(),
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            store[action] = None
            return None
        tag_names = [t.strip() for t in proc.stdout.splitlines() if t.strip()]
        bare_majors: set[int] = set()
        all_majors: set[int] = set()
        for name in tag_names:
            # Bare major: exactly "vN"
            if re.fullmatch(r"v\d+", name):
                try:
                    bare_majors.add(int(name[1:]))
                except ValueError:
                    pass
            m = _MAJOR_TAG_RE.match(name)
            if m:
                try:
                    all_majors.add(int(m.group(1)))
                except ValueError:
                    pass
        max_major = max(all_majors) if all_majors else None
        result: dict = {"bare_majors": bare_majors, "max_major": max_major}
        store[action] = result
        return result
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
    tag_cache: dict[str, dict | None] = {}
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
            # SHA-pinned: report only, never replace.
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
            # Parse current major from pin (vN, vN.x.y). Skip non-version refs.
            m = re.match(r"^v(\d+)", pin)
            if not m:
                continue
            M = int(m.group(1))
            desired = f"v{M}"

            info = action_tag_info(action, cache=tag_cache)
            if info is None:
                # gh absent or API failed — skip this pin.
                continue

            bare_major_exists = M in info["bare_majors"]
            max_major = info["max_major"]

            if pin == desired:
                # Already at floating major. Report newer major if available.
                if max_major is not None and max_major > M:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=f"v{max_major}",
                            status="ok",
                            detail=f"newer major v{max_major} available; not auto-applied (report only)",
                        )
                    )
                # else: up to date, emit nothing
            else:
                # Exact/minor pin (e.g. v3.19.0) — try to collapse to floating major.
                if bare_major_exists:
                    detail = f"normalize to floating major v{M}"
                    if max_major is not None and max_major > M:
                        detail += f"; newer major v{max_major} available"
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=desired,
                            status="ok",
                            detail=detail,
                        )
                    )
                    if apply:
                        replacements.append(
                            PinReplacement(
                                action=action,
                                pin=pin,
                                latest=desired,
                                line=uses.line or None,
                            )
                        )
                else:
                    # No bare major tag upstream — cannot safely collapse.
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=pin,
                            status="ok",
                            detail="no floating major tag upstream; report only",
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
