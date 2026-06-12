"""Human-readable and JSON upgrade reports."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from repo_ops_core.state import cache_get_dirty_skip
from repo_ops_core.table import column_width, format_cell
from repo_ops_models import RepoBundle, WorkspaceResult

from repo_ops_upgrade.discovery import DiscoveryDiagnostics

UpgradeMode = Literal["plan", "apply"]

DEFAULT_MAX_FAILURE_LINES = 20
_DIRTY_SKIP_MARKER = "dirty git worktree"

# Per-manager remediation advice for packages reported behind latest.
# bun/pnpm/yarn honour --latest (which sets their per-ecosystem env var);
# go has a 3-way mode flag; npm has no latest mode at all, so the only
# remediation is a manual pin bump (issue #102).
_LATEST_HINT_BY_MANAGER: dict[str, str] = {
    "bun": "run with --latest to include",
    "pnpm": "run with --latest to include",
    # yarn behind-latest is surfaced only after --latest already ran (the resolver
    # kept these back for peer/group constraints), so re-running --latest won't move
    # them — they need manual attention. (#110)
    "yarn": "peer/group-constrained; --latest can't move them, bump manually",
    "go": "run with --go-upgrade-mode=explicit to include",
}
_LATEST_HINT_NO_MODE = "no latest mode; bump the pin manually"
_LATEST_HINT_DEFAULT = "run with --latest to include"


def build_run_summary(
    bundles: dict[Path, RepoBundle],
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
) -> dict[str, Any]:
    """Aggregate end-of-run counters for human footer and JSON ``summary``."""
    workspaces_ok = 0
    workspaces_fail = 0
    workspaces_skip = 0
    dirty_skips = 0
    verify_failures = 0
    actions_bumps = 0
    actions_failures = 0
    packages_behind_latest = 0
    actions_behind_latest = 0
    packages_upgraded = 0
    behind_latest_by_manager: dict[str, int] = {}

    actions_policy_violations = 0

    for bundle in bundles.values():
        for wr in bundle.workspaces:
            packages_upgraded += len(wr.changes)
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
            n_behind = len(wr.behind_latest)
            packages_behind_latest += n_behind
            if n_behind:
                manager = wr.workspace.manager
                behind_latest_by_manager[manager] = (
                    behind_latest_by_manager.get(manager, 0) + n_behind
                )
        action_totals = bundle.actions_summary()
        actions_bumps += action_totals["bumps"]
        actions_failures += action_totals["failures"]
        actions_behind_latest += action_totals.get("behind_latest", 0)
        actions_policy_violations += action_totals.get("policy_violations", 0)

    discovery_skipped = len(diagnostics.skipped) if diagnostics else 0
    return {
        "workspaces_ok": workspaces_ok,
        "workspaces_fail": workspaces_fail,
        "workspaces_skip": workspaces_skip,
        "dirty_skips": dirty_skips,
        "verify_failures": verify_failures,
        "actions_bumps": actions_bumps,
        "actions_failures": actions_failures,
        "actions_policy_violations": actions_policy_violations,
        "discovery_skipped": discovery_skipped,
        "packages_upgraded": packages_upgraded,
        "packages_behind_latest": packages_behind_latest,
        "packages_behind_latest_by_manager": behind_latest_by_manager,
        "actions_behind_latest": actions_behind_latest,
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


def _enrich_repo_json_with_aging(
    bundle: RepoBundle,
    repo_json: dict[str, Any],
) -> dict[str, Any]:
    """Add dirty-skip aging fields to workspace entries that were dirty-skipped."""
    ws_list = repo_json.get("workspaces", [])
    for ws_json, wr in zip(ws_list, bundle.workspaces, strict=False):
        if wr.status == "skip" and wr.error and _DIRTY_SKIP_MARKER in wr.error:
            entry = cache_get_dirty_skip(wr.workspace.git_repo)
            if entry:
                ws_json["dirty_since"] = entry["first_seen"]
                ws_json["dirty_skip_runs"] = entry["runs"]
    return repo_json


def build_json_payload(
    bundles: dict[Path, RepoBundle],
    *,
    roots: list[str],
    mode: UpgradeMode,
    diagnostics: DiscoveryDiagnostics | None = None,
    submodules: list[dict[str, str]] | None = None,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    sorted_bundles = sorted(bundles.values(), key=lambda x: x.label)
    repos_json = [_enrich_repo_json_with_aging(b, b.to_json()) for b in sorted_bundles]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "roots": roots,
        "mode": mode,
        "summary": build_run_summary(bundles, diagnostics=diagnostics),
        "warnings": list(diagnostics.warnings) if diagnostics else [],
        "skipped": list(diagnostics.skipped) if diagnostics else [],
        "repos": repos_json,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
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
    compact: bool = False,
    exit_code: int | None = None,
) -> None:
    payload = build_json_payload(
        bundles,
        roots=roots,
        mode=mode,
        diagnostics=diagnostics,
        submodules=submodules,
        submodule_pointer_hints=submodule_pointer_hints,
        exit_code=exit_code,
    )
    print(json.dumps(payload, indent=None if compact else 2))


def write_json_report(
    path: Path,
    bundles: dict[Path, RepoBundle],
    *,
    roots: list[str],
    mode: UpgradeMode,
    diagnostics: DiscoveryDiagnostics | None = None,
    submodules: list[dict[str, str]] | None = None,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
    compact: bool = False,
    exit_code: int | None = None,
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
        exit_code=exit_code,
    )
    text = json.dumps(payload, indent=None if compact else 2) + "\n"
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

    def _bundle_upgrade_count(b: RepoBundle) -> int:
        return sum(len(wr.changes) for wr in b.workspaces)

    if only_outdated:
        # Heaviest-first so the most-outdated repos surface at the top;
        # tie-break alphabetically for stable output (#103).
        ordered = sorted(
            bundles.values(),
            key=lambda b: (-_bundle_upgrade_count(b), b.label),
        )
    else:
        ordered = sorted(bundles.values(), key=lambda b: b.label)

    def _is_visible(wr: WorkspaceResult) -> bool:
        # Single display gate shared by header suppression and the row loop so
        # they never disagree (#103). A clean ok workspace is hidden under
        # --only-outdated; everything else is shown.
        return not (
            only_outdated
            and len(wr.changes) == 0
            and not wr.behind_latest
            and wr.status == "ok"
        )

    for repo in ordered:
        visible = [wr for wr in repo.workspaces if _is_visible(wr)]
        # Under --only-outdated --skip-actions a bundle that renders no rows and
        # no actions gets no header (and no trailing blank) at all (#103).
        if not visible and not include_actions:
            continue
        print(f"## {repo.label}")
        for wr in visible:
            ws = wr.workspace
            n = len(wr.changes)
            status = wr.status.upper()
            rel = ws.path
            with contextlib.suppress(ValueError):
                rel = ws.path.relative_to(ws.git_repo)
            behind_n = len(wr.behind_latest)
            print(f"  [{status}] {rel} ({ws.ecosystem}/{ws.manager}) — {n} upgrade(s)")
            if wr.error:
                error_line = wr.error
                if _DIRTY_SKIP_MARKER in error_line:
                    entry = cache_get_dirty_skip(ws.git_repo)
                    if entry:
                        error_line = (
                            f"{error_line} "
                            f"(dirty since {entry['first_seen']}, {entry['runs']} run(s))"
                        )
                print(f"    error: {error_line}")
            if wr.verify_error:
                print(f"    verify: {wr.verify_error}")
            if wr.verify_fail_disposition:
                print(f"    disposition: {wr.verify_fail_disposition}")
            shown_changes = wr.changes[:top_n]
            if shown_changes:
                name_w = column_width([c.name for c in shown_changes], "")
                from_w = column_width([c.from_ver for c in shown_changes], "")
                for c in shown_changes:
                    print(
                        f"    {format_cell(c.name, name_w)} "
                        f"{format_cell(c.from_ver, from_w)} → {c.to}"
                    )
            if len(wr.changes) > top_n:
                print(f"    ... and {len(wr.changes) - top_n} more")
            if behind_n > 0:
                print(
                    f"    behind-latest: {behind_n} package(s) not actioned (latest-mode off)"
                )
                shown_behind = wr.behind_latest[:top_n]
                bname_w = column_width([c.name for c in shown_behind], "")
                bfrom_w = column_width([c.from_ver for c in shown_behind], "")
                for c in shown_behind:
                    print(
                        f"      {format_cell(c.name, bname_w)} "
                        f"{format_cell(c.from_ver, bfrom_w)} → {c.to} (latest)"
                    )
                if behind_n > top_n:
                    print(f"      ... and {behind_n - top_n} more")
            for oa in wr.overrides_audit:
                print(f"    {oa.get('detail', oa)}")
            for rem in wr.remediations:
                if rem.get("status") != "ok" or rem.get("detail"):
                    print(
                        f"    [{rem.get('id')}] {rem.get('detail') or rem.get('status')}"
                    )
        if include_actions:
            summary = repo.actions_summary()
            seen_action_pins: set[tuple[str, str]] = set()
            action_rows: list = []
            policy_rows: list = []
            for ab in repo.actions:
                if ab.status == "policy":
                    policy_rows.append(ab)
                    continue
                if ab.pin == ab.latest and ab.status == "ok":
                    continue
                key = (ab.action, ab.pin)
                if key in seen_action_pins:
                    continue
                seen_action_pins.add(key)
                action_rows.append(ab)
            if action_rows or policy_rows:
                print("  Actions:")
                for ab in action_rows:
                    # Rows apply never rewrites carry a "report only" detail;
                    # printing their status ("ok") reads as an applied bump.
                    marker = (
                        "report-only"
                        if ab.detail and "report only" in ab.detail
                        else ab.status
                    )
                    print(
                        f"    {ab.workflow}: {ab.action}@{ab.pin} → {ab.latest} [{marker}]"
                    )
                for ab in policy_rows:
                    explanation = ab.detail or "org policy violation"
                    print(
                        f"    {ab.workflow}: {ab.action}@{ab.pin} [policy] {explanation}"
                    )
            print(
                "  Actions: "
                f"{summary['bumps']} bump(s) "
                f"({summary['workflows_scanned']} workflow(s) scanned)"
            )
            policy_count = summary.get("policy_violations", 0)
            if policy_count:
                print(f"  Actions policy violations: {policy_count}")
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
        print(f"  {entry['path']} → {entry['git_repo']} (parent {entry['parent']})")
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
    summary: dict[str, Any],
    failures: list[tuple[str, str]],
    exit_code: int,
    max_failures: int = DEFAULT_MAX_FAILURE_LINES,
    show_skips: bool = False,
    submodule_pointer_hints: list[dict[str, str]] | None = None,
    discovery_skips: list[dict[str, str]] | None = None,
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
        ("packages upgraded", summary.get("packages_upgraded", 0)),
        ("workspaces ok", summary["workspaces_ok"]),
        ("workspaces fail", summary["workspaces_fail"]),
        ("workspaces skip", summary["workspaces_skip"]),
        ("dirty skips", summary["dirty_skips"]),
        ("verify failures", summary["verify_failures"]),
        ("actions bumps", summary["actions_bumps"]),
        ("actions failures", summary["actions_failures"]),
        ("actions policy violations", summary.get("actions_policy_violations", 0)),
        ("discovery skipped", summary["discovery_skipped"]),
    ]
    width = max(len(label) for label, _ in rows)
    for label, count in rows:
        print(f"  {label:<{width}}  {count}")

    # Sentence-form total package-upgrade count across all workspaces (#103).
    pkgs = summary.get("packages_upgraded", 0)
    workspaces_total = (
        summary["workspaces_ok"]
        + summary["workspaces_fail"]
        + summary["workspaces_skip"]
    )
    print(f"  {pkgs} package(s) upgraded across {workspaces_total} workspace(s)")

    packages_behind = summary.get("packages_behind_latest", 0)
    actions_behind = summary.get("actions_behind_latest", 0)
    total_behind = packages_behind + actions_behind
    if total_behind > 0:
        parts: list[str] = []
        if packages_behind > 0:
            by_manager: dict[str, int] = summary.get(
                "packages_behind_latest_by_manager", {}
            )
            if by_manager:
                # One line per manager so the remediation advice is correct for
                # each (npm has no latest mode; bun/pnpm/yarn honour --latest).
                for manager in sorted(by_manager):
                    count = by_manager[manager]
                    if manager == "npm":
                        manager_hint = _LATEST_HINT_NO_MODE
                    else:
                        manager_hint = _LATEST_HINT_BY_MANAGER.get(
                            manager, _LATEST_HINT_DEFAULT
                        )
                    parts.append(
                        f"{count} {manager} package(s) behind latest ({manager_hint})"
                    )
            else:
                parts.append(
                    f"{packages_behind} package(s) behind latest"
                    f" ({_LATEST_HINT_DEFAULT})"
                )
        if actions_behind > 0:
            parts.append(
                f"{actions_behind} action(s) have a newer major available"
                " (apply with --actions-upgrade-mode=latest or --latest;"
                " report-only when no floating major tag exists upstream)"
            )
        print()
        for part in parts:
            print(f"  behind-latest: {part}")

    if failures:
        print()
        print("Failures:")
        shown = failures[: max(0, max_failures)]
        for label, reason in shown:
            print(f"  {label}: {reason}")
        remaining = len(failures) - len(shown)
        if remaining > 0:
            print(f"  ... and {remaining} more")

    if discovery_skips:
        print()
        print("Discovery skips (manifest found but not actionable):")
        shown_skips = discovery_skips[: max(0, max_failures)]
        for entry in shown_skips:
            eco = entry.get("ecosystem", "?")
            print(f"  [{eco}] {entry.get('path', '')}: {entry.get('reason', '')}")
        remaining_skips = len(discovery_skips) - len(shown_skips)
        if remaining_skips > 0:
            print(f"  ... and {remaining_skips} more")

    print()
    print(f"failures: {summary['failure_count']}")
    if show_skips and summary["workspaces_skip"] > 0:
        print(f"skips: {summary['workspaces_skip']}")
    print(f"exit_code: {exit_code}")
