"""Human-readable and JSON upgrade reports."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from repo_ops_models import RepoBundle

from repo_ops_upgrade.discovery import DiscoveryDiagnostics

UpgradeMode = Literal["plan", "apply"]

DEFAULT_MAX_FAILURE_LINES = 20
_DIRTY_SKIP_MARKER = "dirty git worktree"


def build_run_summary(
    bundles: dict[Path, RepoBundle],
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
) -> dict[str, int]:
    """Aggregate end-of-run counters for human footer and JSON ``summary``."""
    workspaces_ok = 0
    workspaces_fail = 0
    workspaces_skip = 0
    dirty_skips = 0
    verify_failures = 0
    actions_bumps = 0
    actions_failures = 0

    for bundle in bundles.values():
        for wr in bundle.workspaces:
            if wr.status == "ok":
                workspaces_ok += 1
            elif wr.status == "fail":
                workspaces_fail += 1
            elif wr.status == "skip":
                workspaces_skip += 1
                if wr.error and _DIRTY_SKIP_MARKER in wr.error:
                    dirty_skips += 1
            if wr.verify_error:
                verify_failures += 1
        action_totals = bundle.actions_summary()
        actions_bumps += action_totals["bumps"]
        actions_failures += action_totals["failures"]

    discovery_skipped = len(diagnostics.skipped) if diagnostics else 0
    return {
        "workspaces_ok": workspaces_ok,
        "workspaces_fail": workspaces_fail,
        "workspaces_skip": workspaces_skip,
        "dirty_skips": dirty_skips,
        "verify_failures": verify_failures,
        "actions_bumps": actions_bumps,
        "actions_failures": actions_failures,
        "discovery_skipped": discovery_skipped,
        "failure_count": count_failures(bundles),
    }


def collect_run_failures(
    bundles: dict[Path, RepoBundle],
) -> list[tuple[str, str]]:
    """Return ``(repo_label, reason)`` rows for failed workspaces and Actions."""
    items: list[tuple[str, str]] = []
    for bundle in sorted(bundles.values(), key=lambda b: b.label):
        for wr in bundle.workspaces:
            if wr.status != "fail":
                continue
            reason = wr.verify_error or wr.error or "failed"
            items.append((bundle.label, reason))
        for ab in bundle.actions:
            if ab.status != "fail":
                continue
            reason = ab.detail or f"{ab.workflow}: {ab.action}"
            items.append((bundle.label, reason))
    return items


def build_json_payload(
    bundles: dict[Path, RepoBundle],
    *,
    roots: list[str],
    mode: UpgradeMode,
    diagnostics: DiscoveryDiagnostics | None = None,
    submodules: list[dict[str, str]] | None = None,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "roots": roots,
        "mode": mode,
        "summary": build_run_summary(bundles, diagnostics=diagnostics),
        "warnings": list(diagnostics.warnings) if diagnostics else [],
        "skipped": list(diagnostics.skipped) if diagnostics else [],
        "repos": [b.to_json() for b in sorted(bundles.values(), key=lambda x: x.label)],
    }
    if submodules is not None:
        payload["submodules"] = submodules
    if submodule_pointer_hints is not None:
        payload["submodule_pointer_hints"] = submodule_pointer_hints
    return payload


def print_json_report(
    bundles: dict[Path, RepoBundle],
    *,
    roots: list[str],
    mode: UpgradeMode,
    diagnostics: DiscoveryDiagnostics | None = None,
    submodules: list[dict[str, str]] | None = None,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
) -> None:
    payload = build_json_payload(
        bundles,
        roots=roots,
        mode=mode,
        diagnostics=diagnostics,
        submodules=submodules,
        submodule_pointer_hints=submodule_pointer_hints,
    )
    print(json.dumps(payload, indent=2))


def write_json_report(
    path: Path,
    bundles: dict[Path, RepoBundle],
    *,
    roots: list[str],
    mode: UpgradeMode,
    diagnostics: DiscoveryDiagnostics | None = None,
    submodules: list[dict[str, str]] | None = None,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
) -> Path:
    """Write one JSON document to path via temp file + rename. Returns resolved path."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_json_payload(
        bundles,
        roots=roots,
        mode=mode,
        diagnostics=diagnostics,
        submodules=submodules,
        submodule_pointer_hints=submodule_pointer_hints,
    )
    text = json.dumps(payload, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return path


def print_human_report(
    bundles: dict[Path, RepoBundle],
    *,
    dry_run: bool,
    only_outdated: bool,
    top_n: int,
    include_actions: bool,
) -> None:
    mode = "plan" if dry_run else "apply"
    print(f"repo-ops {mode}")
    print()
    for repo in sorted(bundles.values(), key=lambda b: b.label):
        print(f"## {repo.label}")
        for wr in repo.workspaces:
            ws = wr.workspace
            n = len(wr.changes)
            if only_outdated and n == 0 and wr.status == "ok":
                continue
            status = wr.status.upper()
            rel = ws.path
            try:
                rel = ws.path.relative_to(ws.git_repo)
            except ValueError:
                pass
            print(f"  [{status}] {rel} ({ws.ecosystem}/{ws.manager}) — {n} upgrade(s)")
            if wr.error:
                print(f"    error: {wr.error}")
            if wr.verify_error:
                print(f"    verify: {wr.verify_error}")
            if wr.verify_fail_disposition:
                print(f"    disposition: {wr.verify_fail_disposition}")
            for c in wr.changes[:top_n]:
                print(f"    {c.name} {c.from_ver} → {c.to}")
            if len(wr.changes) > top_n:
                print(f"    ... and {len(wr.changes) - top_n} more")
            for oa in wr.overrides_audit:
                print(f"    {oa.get('detail', oa)}")
            for rem in wr.remediations:
                if rem.get("status") != "ok" or rem.get("detail"):
                    print(
                        f"    [{rem.get('id')}] {rem.get('detail') or rem.get('status')}"
                    )
        if include_actions:
            summary = repo.actions_summary()
            if repo.actions:
                print("  Actions:")
                for ab in repo.actions:
                    print(
                        f"    {ab.workflow}: {ab.action}@{ab.pin} → {ab.latest} [{ab.status}]"
                    )
            print(
                "  Actions: "
                f"{summary['bumps']} bump(s) "
                f"({summary['workflows_scanned']} workflow(s) scanned)"
            )
        print()


def print_submodule_section(
    submodules: list[dict[str, str]],
    *,
    pointer_hints: list[dict[str, str]] | None = None,
) -> None:
    """Human-readable submodule umbrella notes (plan/apply)."""
    if not submodules and not pointer_hints:
        return
    print("Submodules")
    for entry in submodules:
        print(
            f"  {entry['path']} → {entry['git_repo']} "
            f"(parent {entry['parent']})"
        )
    if pointer_hints:
        print()
        print("Parent repos needing submodule pointer commit:")
        for hint in pointer_hints:
            paths = hint.get("paths", "")
            print(f"  {hint['parent']}: {paths}")
            if hint.get("hint"):
                print(f"    {hint['hint']}")
    print()


def count_failures(bundles: dict[Path, RepoBundle]) -> int:
    """Count workspace and Actions rows with status fail."""
    n = 0
    for bundle in bundles.values():
        n += sum(1 for wr in bundle.workspaces if wr.status == "fail")
        n += sum(1 for ab in bundle.actions if ab.status == "fail")
    return n


def count_workspace_skips(bundles: dict[Path, RepoBundle]) -> int:
    return sum(
        1
        for bundle in bundles.values()
        for wr in bundle.workspaces
        if wr.status == "skip"
    )


def any_workspace_skip_or_fail(bundles: dict[Path, RepoBundle]) -> bool:
    return any(
        wr.status in ("fail", "skip")
        for bundle in bundles.values()
        for wr in bundle.workspaces
    )


def print_run_footer(
    *,
    summary: dict[str, int],
    failures: list[tuple[str, str]],
    exit_code: int,
    max_failures: int = DEFAULT_MAX_FAILURE_LINES,
    show_skips: bool = False,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
) -> None:
    """End-of-run roll-up and automation lines (human stdout)."""
    if submodule_pointer_hints:
        print("---")
        print("Submodule pointers")
        for hint in submodule_pointer_hints:
            paths = hint.get("paths", "")
            print(f"  {hint['parent']}: {paths}")
            if hint.get("hint"):
                print(f"    {hint['hint']}")
        print()
    print("---")
    print("Summary")
    rows = [
        ("workspaces ok", summary["workspaces_ok"]),
        ("workspaces fail", summary["workspaces_fail"]),
        ("workspaces skip", summary["workspaces_skip"]),
        ("dirty skips", summary["dirty_skips"]),
        ("verify failures", summary["verify_failures"]),
        ("actions bumps", summary["actions_bumps"]),
        ("discovery skipped", summary["discovery_skipped"]),
    ]
    width = max(len(label) for label, _ in rows)
    for label, count in rows:
        print(f"  {label:<{width}}  {count}")

    if failures:
        print()
        print("Failures:")
        shown = failures[: max(0, max_failures)]
        for label, reason in shown:
            print(f"  {label}: {reason}")
        remaining = len(failures) - len(shown)
        if remaining > 0:
            print(f"  ... and {remaining} more")

    print()
    print(f"failures: {summary['failure_count']}")
    if show_skips and summary["workspaces_skip"] > 0:
        print(f"skips: {summary['workspaces_skip']}")
    print(f"exit_code: {exit_code}")
