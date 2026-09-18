"""Ecosystem-agnostic remediation framework: types, registry, dispatch."""

from __future__ import annotations

import importlib
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


def make_fmt_hook(
    *,
    hook_id: str,
    ecosystem: str,
    ecosystem_label: str,
    config_names: tuple[str, ...],
    extensions: tuple[str, ...],
    tool: str,
    which_fn: Callable[[str], str | None],
    run_fn: Callable[[list[str], Path, float], Any],
    build_argv: Callable[[list[str] | None], list[str]],
    fail_label: str,
    no_changes_apply_detail: str,
    detailed_report: bool = False,
    plan_message: str = "",
    success_label: str = "",
    no_changes_plan_detail: str = "",
) -> RemediationHook:
    """Build a "run the formatter on files changed since HEAD" remediation hook.

    Shared by gofmt / deno fmt / cargo fmt, which differ only in tool name,
    config-file gate, formattable extensions, and invocation argv.

    *which_fn* and *run_fn* must be small wrappers defined in the calling
    ecosystem module (not passed as ``shutil.which``/``run`` directly) so
    that ``unittest.mock.patch`` targeting that module's own ``shutil`` or
    ``run`` name — as the test suite does per ecosystem — still takes
    effect; a reference captured here at hook-build time would not observe
    a patch applied to the ecosystem module afterwards.

    *detailed_report* reproduces gofmt's behaviour of describing the actual
    scoped invocation (and a distinct no-op message) in both plan-mode
    reporting and the applied-success detail. The other formatters instead
    report a fixed *plan_message* / *success_label* regardless of which
    files were scoped — an intentional asymmetry, not an oversight.
    """

    def _has_config(path: Path) -> bool:
        return any((path / name).exists() for name in config_names)

    def _changed(ws: Workspace) -> list[str] | None:
        return changed_files_since_head(ws, extensions, exclude_names=config_names)

    def detect(ws: Workspace, ctx: dict[str, Any]) -> bool:
        if ws.ecosystem != ecosystem:
            return False
        if not _has_config(ws.path):
            return False
        if which_fn(tool) is None:
            return False  # tool not available — skip, do not fail
        changes = ctx.get("changes", [])
        return bool(changes) or not ctx.get("apply", True)

    def report(ws: Workspace, ctx: dict[str, Any]) -> list[str]:
        if not detailed_report:
            return [plan_message]
        files = _changed(ws)
        if files is not None and not files:
            return [no_changes_plan_detail]
        argv = " ".join(build_argv(files))
        return [f"REMEDIATE {hook_id}: would run {argv} (normalize {ecosystem_label} formatting)"]

    def apply(ws: Workspace, ctx: dict[str, Any]) -> ApplyResult:
        files = _changed(ws)
        if files is not None and not files:
            return True, no_changes_apply_detail
        cmd = build_argv(files)
        proc = run_fn(cmd, ws.path, 120)
        if proc.returncode == 0:
            detail = f"{' '.join(cmd)} (ok)" if detailed_report else success_label
            return True, detail
        err = stderr_tail(proc.stderr or proc.stdout or "")
        return False, f"{fail_label} (failed: {err})"

    return RemediationHook(hook_id, detect, report, apply)


# Global registry — populated by ecosystem packages on import.
_GLOBAL_REGISTRY: list[RemediationHook] = []


def register(hook: RemediationHook) -> None:
    """Register *hook* in the global dispatch registry."""
    _GLOBAL_REGISTRY.append(hook)


# Ecosystem packages whose import populates the global registry.
ECOSYSTEM_MODULES = (
    "repo_ops_upgrade_remediations_deno",
    "repo_ops_upgrade_remediations_go",
    "repo_ops_upgrade_remediations_js",
    "repo_ops_upgrade_remediations_py",
    "repo_ops_upgrade_remediations_rust",
)


def load_ecosystem_hooks() -> None:
    """Import every ecosystem package so its hooks reach the global registry.

    Imported by name: the packages import this module to call :func:`register`,
    so naming them as imports here would be a cycle. Repeat calls are free —
    ``sys.modules`` makes the second import a lookup, so hooks register once.
    """
    for name in ECOSYSTEM_MODULES:
        importlib.import_module(name)


def run_hooks(
    hooks: list[RemediationHook],
    result: WorkspaceResult,
    *,
    apply: bool,
) -> None:
    """Run *hooks* against *result* in order.

    Each hook's ``detect`` is called first; if True the hook either reports
    (plan mode) or applies (apply mode).  Exceptions are caught per-hook so
    one misbehaving hook cannot abort the others.
    """
    ctx: dict[str, Any] = {
        "changes": result.changes,
        "apply": apply,
        "pre_apply_snapshot": result.pre_apply_snapshot,
    }
    for hook in hooks:
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


def make_run_remediations(
    hooks: Callable[[], list[RemediationHook]],
) -> Callable[..., None]:
    """Build a ``run_remediations(result, *, apply)`` for one ecosystem.

    *hooks* is called fresh on every invocation rather than captured once, so
    a test that patches an ecosystem module's own ``REMEDIATIONS`` list (to
    isolate it from the global registry) still takes effect through the
    returned wrapper — pass e.g. ``lambda: REMEDIATIONS``.
    """

    def run_remediations(
        result: WorkspaceResult,
        *,
        apply: bool,
    ) -> None:
        run_hooks(hooks(), result, apply=apply)

    return run_remediations


def dispatch(
    result: WorkspaceResult,
    *,
    apply: bool,
    _registry: list[RemediationHook] | None = None,
) -> None:
    """Dispatch remediations from the global registry (or *_registry* if given).

    All hooks registered via :func:`register` across every imported ecosystem
    package are executed.  Pass *_registry* in tests to supply an isolated list
    without touching the global state.
    """
    hooks = _registry if _registry is not None else list(_GLOBAL_REGISTRY)
    run_hooks(hooks, result, apply=apply)
