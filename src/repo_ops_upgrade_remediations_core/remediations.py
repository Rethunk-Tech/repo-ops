"""Ecosystem-agnostic remediation framework: types, registry, dispatch."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_ops_core import process
from repo_ops_models import Workspace, WorkspaceResult

ApplyResult = tuple[bool, str]


@dataclass
class RemediationHook:
    """A single post-upgrade remediation hook.

    Fields
    ------
    id      : Unique name used for skip-set filtering and result tagging.
    detect  : Return True when this remediation should run for the workspace.
    report  : Return human-readable lines describing what *would* be done.
    apply   : Perform the remediation; return (ok, detail_message).
    files   : Optional; return workspace-relative file paths affected.
               Omit (None) when no specific files are targeted.
    """

    id: str
    detect: Callable[[Any, dict[str, Any]], bool]
    report: Callable[[Any, dict[str, Any]], list[str]]
    apply: Callable[[Any, dict[str, Any]], ApplyResult]
    files: Callable[[Any], list[str]] | None = field(default=None)


def stderr_tail(text: str, *, max_chars: int = 200) -> str:
    """Return a short trailing snippet of *text* for remediation failure details."""
    s = (text or "").strip()
    if not s:
        return "non-zero exit"
    return s if len(s) <= max_chars else s[-max_chars:]


def changed_files_since_head(
    ws: Workspace,
    extensions: tuple[str, ...],
    exclude_names: Iterable[str] = (),
) -> list[str] | None:
    """Return absolute paths of *extensions* files changed since HEAD in *ws*.

    Only files within *ws.path* are returned.  Returns ``None`` when git is
    unavailable or this is not a git repository — the caller should fall back
    to whole-workspace formatting.  Returns an empty list when git worked but
    no matching source files changed (typical for a pure dep bump that only
    touched a manifest/lockfile).  Files whose basename is in *exclude_names*
    are dropped (e.g. a formatter's own config/lock files, which are not a
    "source" reformat).

    Uses ``subprocess`` directly (not the repo_ops_core.process.run helper)
    so that tests that mock the process helper can still exercise the
    fallback path without interference from git calls.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(ws.git_repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    exclude = set(exclude_names)
    changed: list[str] = []
    for rel in proc.stdout.splitlines():
        if not rel.endswith(extensions):
            continue
        if Path(rel).name in exclude:
            continue
        abs_path = ws.git_repo / rel
        try:
            abs_path.relative_to(ws.path)
        except ValueError:
            continue
        if abs_path.exists():
            changed.append(str(abs_path))
    return changed


# Global registry — populated by ecosystem packages on import.
_GLOBAL_REGISTRY: list[RemediationHook] = []


def register(hook: RemediationHook) -> None:
    """Register *hook* in the global dispatch registry."""
    _GLOBAL_REGISTRY.append(hook)


def run_hooks(
    hooks: list[RemediationHook],
    result: WorkspaceResult,
    *,
    apply: bool,
    skip: set[str],
) -> None:
    """Run *hooks* against *result* in order.

    Each hook's ``detect`` is called first; if True the hook either reports
    (plan mode) or applies (apply mode).  Exceptions are caught per-hook so
    one misbehaving hook cannot abort the others.
    """
    ctx: dict[str, Any] = {"changes": result.changes, "apply": apply}
    for hook in hooks:
        if hook.id in skip:
            continue
        entry: dict[str, Any] = {"id": hook.id, "status": "ok", "detail": ""}
        try:
            detected = hook.detect(result.workspace, ctx)
            if process.VERBOSE:
                print(
                    f"  remediation {hook.id}: detect={detected}",
                    file=sys.stderr,
                    flush=True,
                )
            if detected:
                if hook.files is not None:
                    files = hook.files(result.workspace)
                    if files:
                        entry["files"] = files
                if apply:
                    ok, entry["detail"] = hook.apply(result.workspace, ctx)
                    entry["status"] = "applied" if ok else "fail"
                    if not ok:
                        result.status = "fail"
                else:
                    lines = hook.report(result.workspace, ctx)
                    entry["detail"] = "; ".join(lines)
                    entry["status"] = "pending"
            else:
                entry["status"] = "ok"
        except FileNotFoundError as exc:
            # Remediation tool is not installed.  Treat as skip so that a
            # missing formatter never fails an otherwise-successful dep bump.
            entry["status"] = "skip"
            entry["detail"] = f"tool not found: {exc}"
        except Exception as exc:
            entry["status"] = "fail"
            entry["detail"] = str(exc)
            if apply:
                result.status = "fail"
        if entry.get("status") != "ok" or entry.get("detail"):
            result.remediations.append(entry)


def dispatch(
    result: WorkspaceResult,
    *,
    apply: bool,
    skip: set[str],
    _registry: list[RemediationHook] | None = None,
) -> None:
    """Dispatch remediations from the global registry (or *_registry* if given).

    All hooks registered via :func:`register` across every imported ecosystem
    package are executed.  Pass *_registry* in tests to supply an isolated list
    without touching the global state.

    ``go_generate`` is **default-off** on this production path: it is added to
    the effective skip set unless ``REPO_OPS_REMEDIATE_GO_GENERATE=1`` is set.
    (Ecosystem ``go.run_remediations`` applies the same gate for direct callers.)
    """
    effective_skip = set(skip)
    if os.environ.get("REPO_OPS_REMEDIATE_GO_GENERATE", "") != "1":
        effective_skip.add("go_generate")
    hooks = _registry if _registry is not None else list(_GLOBAL_REGISTRY)
    run_hooks(hooks, result, apply=apply, skip=effective_skip)
