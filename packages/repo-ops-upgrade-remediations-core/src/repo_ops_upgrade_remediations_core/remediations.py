"""Ecosystem-agnostic remediation framework: types, registry, dispatch."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from repo_ops_core import process
from repo_ops_models import WorkspaceResult

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
    """
    hooks = _registry if _registry is not None else list(_GLOBAL_REGISTRY)
    run_hooks(hooks, result, apply=apply, skip=skip)
