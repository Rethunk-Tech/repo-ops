"""Human-readable and JSON upgrade reports."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from repo_ops_core.holds import HoldRule
from repo_ops_core.state import cache_get_dirty_skip, cache_get_staleness
from repo_ops_core.table import column_width, format_cell
from repo_ops_js.holds import resolve_package_hold
from repo_ops_models import DIRTY_SKIP_MARKER, Change, RepoBundle, WorkspaceResult
from repo_ops_upgrade.discovery import DiscoveryDiagnostics

UpgradeMode = Literal["plan", "apply"]

_MAX_FAILURE_LINES = 20
_TOP_CHANGES = 10

# Generic behind-latest note for a manager with no specific wording below.
_LATEST_HINT_DEFAULT = "could not be moved automatically — bump manually"
# Per-manager behind-latest note: every sweep attempts a targeted bump, so the
# leftovers are packages the fallback could not move.
_TARGETED_TRIED_HINT: dict[str, str] = {
    "yarn": (
        "peer/group-constrained; the targeted bump was attempted but could "
        "not move them (verify failed or still constrained) — bump manually"
    ),
    "npm": (
        "no latest mode; the targeted bump was attempted but could not move "
        "them (install failed or version did not advance) — bump manually"
    ),
    "pnpm": (
        "the targeted bump was attempted but could not move them "
        "(install failed or still constrained) — bump manually"
    ),
    "bun": (
        "the targeted bump was attempted but could not move them "
        "(install failed or still constrained) — bump manually"
    ),
    "deno": (
        "the targeted bump was attempted but could not move them "
        "(install failed or version did not advance) — bump manually"
    ),
    "rust": (
        "the targeted bump was attempted but could not move them "
        "(cargo failed or the lockfile did not advance) — bump manually"
    ),
    "poetry": (
        "the targeted bump was attempted but could not move them "
        "(poetry add failed or the lockfile did not land the target) — bump manually"
    ),
    "pdm": (
        "the targeted bump was attempted but could not move them "
        "(pdm add failed or the lockfile did not land the target) — bump manually"
    ),
    "pipenv": (
        "the targeted bump was attempted but could not move them "
        "(pipenv install failed or the lockfile did not land the target) — bump manually"
    ),
    "pip-tools": (
        "the targeted bump was attempted but could not move them "
        "(compile failed or the requirements.in constraint still blocked the target) — "
        "bump manually"
    ),
}


def _behind_latest_hint(manager: str) -> str:
    """Per-manager remediation note for behind-latest packages.

    Shared by the per-workspace line and the run footer so the two never disagree.
    """
    return _TARGETED_TRIED_HINT.get(manager, _LATEST_HINT_DEFAULT)


# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules — surfacing a hold-declined bump in
# the behind-latest bucket it already lands in, rather than a new mechanism.
# ---------------------------------------------------------------------------


def _behind_latest_holds(wr: WorkspaceResult) -> list[HoldRule | None]:
    """Active hold (if any) per ``wr.behind_latest`` entry, same order.

    Shared by the row annotation and the summary counter so "held" means the
    same thing in both places.
    """
    return _holds(wr, wr.behind_latest)


def _holds(wr: WorkspaceResult, changes: list[Change]) -> list[HoldRule | None]:
    ws = wr.workspace
    return [
        resolve_package_hold(replace(ws, path=ws.path / c.member) if c.member else ws, c.name)
        for c in changes
    ]


def _expo_reason(rule: HoldRule | None) -> str | None:
    """Why an Expo-derived hold applies; configured holds keep their reason out of the report."""
    return rule.reason if rule is not None and rule.source == "expo" else None


def _format_behind_latest_annotation(rule: HoldRule | None) -> str:
    if rule is None:
        return "(latest)"
    annotation = f"(latest; held <= {rule.max_version}"
    if rule.until:
        annotation += f", until {rule.until}"
    if reason := _expo_reason(rule):
        annotation += f", {reason}"
    return annotation + ")"


def build_run_summary(
    bundles: dict[Path, RepoBundle],
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
    excluded_count: int = 0,
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
    packages_held = 0
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
                if wr.error and DIRTY_SKIP_MARKER in wr.error:
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
                packages_held += sum(1 for rule in _behind_latest_holds(wr) if rule is not None)
        action_totals = bundle.actions_summary()
        actions_bumps += action_totals["bumps"]
        actions_failures += action_totals["failures"]
        actions_behind_latest += action_totals.get("behind_latest", 0)
        actions_policy_violations += action_totals.get("policy_violations", 0)

    discovery_skipped = len(diagnostics.skipped) if diagnostics else 0
    summary: dict[str, Any] = {
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
        "packages_held": packages_held,
        "actions_behind_latest": actions_behind_latest,
        "failure_count": count_failures(bundles),
        "excluded": excluded_count,
    }
    # Top-3 staleness scores (behind-count x days-behind, from the state
    # store).  Only attached when at least one repo scores above zero, so an
    # empty store leaves the summary dict — and every consumer's output —
    # unchanged when no repo has a score.
    staleness_top = _collect_staleness_top(bundles)
    if staleness_top:
        summary["staleness_top"] = staleness_top
    return summary


def _collect_staleness_top(
    bundles: dict[Path, RepoBundle],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Top-``limit`` repos by staleness score (score > 0 only), best first."""
    today = date.today()
    scored: list[dict[str, Any]] = []
    for bundle in bundles.values():
        info = cache_get_staleness(bundle.git_repo, today)
        if info and info["staleness_score"] > 0:
            scored.append({"repo": bundle.label, "score": info["staleness_score"]})
    scored.sort(key=lambda item: (-item["score"], item["repo"]))
    return scored[:limit]


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
    """Add ``held`` to held behind-latest entries and aging fields to dirty-skipped workspaces."""
    ws_list = repo_json.get("workspaces", [])
    for ws_json, wr in zip(ws_list, bundle.workspaces, strict=False):
        for entry, rule in zip(ws_json["behind_latest"], _behind_latest_holds(wr), strict=True):
            if rule is not None:
                entry["held"] = rule.max_version
            if reason := _expo_reason(rule):
                entry["hold_reason"] = reason
        for entry, rule in zip(ws_json["changes"], _holds(wr, wr.changes), strict=True):
            if reason := _expo_reason(rule):
                entry["hold_reason"] = reason
        if wr.status == "skip" and wr.error and DIRTY_SKIP_MARKER in wr.error:
            entry = cache_get_dirty_skip(wr.workspace.git_repo)
            if entry:
                ws_json["dirty_since"] = entry["first_seen"]
                ws_json["dirty_skip_runs"] = entry["runs"]
    return repo_json


def _enrich_repo_json_with_staleness(
    bundle: RepoBundle,
    repo_json: dict[str, Any],
) -> dict[str, Any]:
    """Add repo-level staleness fields read back from the state store.

    Sibling of :func:`_enrich_repo_json_with_aging`.  Fields are only added
    when the store tracks the repo (it was seen behind latest at some point),
    so payloads from an empty store are unchanged:

    - ``staleness_score`` — behind-count x days since first seen behind
    - ``new_behind_since_last_run`` — behind-latest packages that appeared
      since the previous recorded run
    """
    info = cache_get_staleness(bundle.git_repo, date.today())
    if info is not None:
        repo_json["staleness_score"] = info["staleness_score"]
        repo_json["new_behind_since_last_run"] = info["new_behind_since_last_run"]
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
    excluded_count: int = 0,
) -> dict[str, Any]:
    sorted_bundles = sorted(bundles.values(), key=lambda x: x.label)
    repos_json = [
        _enrich_repo_json_with_staleness(b, _enrich_repo_json_with_aging(b, b.to_json()))
        for b in sorted_bundles
    ]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "roots": roots,
        "mode": mode,
        "summary": build_run_summary(
            bundles,
            diagnostics=diagnostics,
            excluded_count=excluded_count,
        ),
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


def print_human_report(
    bundles: dict[Path, RepoBundle],
    *,
    dry_run: bool,
) -> None:
    top_n = _TOP_CHANGES
    mode = "plan" if dry_run else "apply"
    print(f"repo-ops {mode}")
    print()

    def _bundle_upgrade_count(b: RepoBundle) -> int:
        return sum(len(wr.changes) for wr in b.workspaces)

    # Heaviest-first so the most-outdated repos surface at the top;
    # tie-break alphabetically for stable output.
    ordered = sorted(bundles.values(), key=lambda b: (-_bundle_upgrade_count(b), b.label))

    def _is_visible(wr: WorkspaceResult) -> bool:
        # A clean ok workspace is hidden; everything else is shown.
        return not (
            len(wr.changes) == 0
            and not wr.behind_latest
            and wr.status == "ok"
            and not wr.remediations
        )

    for repo in ordered:
        visible = [wr for wr in repo.workspaces if _is_visible(wr)]
        summary = repo.actions_summary()
        seen_action_pins: set[tuple[str, str]] = set()
        action_rows: list = []
        policy_rows: list = []
        for ab in repo.actions:
            if ab.status in ("policy", "policy-blocked", "warn"):
                policy_rows.append(ab)
                continue
            if ab.pin == ab.latest and ab.status == "ok":
                continue
            key = (ab.action, ab.pin)
            if key in seen_action_pins:
                continue
            seen_action_pins.add(key)
            action_rows.append(ab)
        # A repo with nothing to act on gets no section at all.
        if not visible and not action_rows and not policy_rows:
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
                if DIRTY_SKIP_MARKER in error_line:
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
                for c, rule in zip(shown_changes, _holds(wr, shown_changes), strict=True):
                    reason = _expo_reason(rule)
                    print(
                        f"    {format_cell(c.name, name_w)} "
                        f"{format_cell(c.from_ver, from_w)} → {c.to}"
                        + (f" ({reason})" if reason else "")
                    )
            if len(wr.changes) > top_n:
                print(f"    ... and {len(wr.changes) - top_n} more")
            if behind_n > 0:
                hint = _behind_latest_hint(ws.manager)
                behind_holds = _behind_latest_holds(wr)
                held_n = sum(1 for rule in behind_holds if rule is not None)
                held_suffix = f", {held_n} held" if held_n else ""
                print(
                    f"    behind-latest: {behind_n} package(s) not actioned{held_suffix} ({hint})"
                )
                shown_behind = wr.behind_latest[:top_n]
                shown_holds = behind_holds[:top_n]
                bname_w = column_width([c.name for c in shown_behind], "")
                bfrom_w = column_width([c.from_ver for c in shown_behind], "")
                for c, rule in zip(shown_behind, shown_holds, strict=True):
                    annotation = _format_behind_latest_annotation(rule)
                    print(
                        f"      {format_cell(c.name, bname_w)} "
                        f"{format_cell(c.from_ver, bfrom_w)} → {c.to} {annotation}"
                    )
                if behind_n > top_n:
                    print(f"      ... and {behind_n - top_n} more")
            for oa in wr.overrides_audit:
                print(f"    {oa.get('detail', oa)}")
            for rem in wr.remediations:
                if rem.get("status") != "ok" or rem.get("detail"):
                    print(f"    [{rem.get('id')}] {rem.get('detail') or rem.get('status')}")
        if action_rows or policy_rows:
            print("  Actions:")
            for ab in action_rows:
                # Rows apply never rewrites carry a "report only" detail;
                # printing their status ("ok") reads as an applied bump.
                marker = "report-only" if ab.detail and "report only" in ab.detail else ab.status
                print(f"    {ab.workflow}: {ab.action}@{ab.pin} → {ab.latest} [{marker}]")
            for ab in policy_rows:
                explanation = ab.detail or "org policy violation"
                print(f"    {ab.workflow}: {ab.action}@{ab.pin} [{ab.status}] {explanation}")
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


def print_run_footer(
    *,
    summary: dict[str, Any],
    failures: list[tuple[str, str]],
    exit_code: int,
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
    if summary.get("excluded", 0):
        rows.append(("excluded", summary["excluded"]))
    width = max(len(label) for label, _ in rows)
    for label, count in rows:
        print(f"  {label:<{width}}  {count}")

    # Sentence-form total package-upgrade count across all workspaces.
    pkgs = summary.get("packages_upgraded", 0)
    workspaces_total = (
        summary["workspaces_ok"] + summary["workspaces_fail"] + summary["workspaces_skip"]
    )
    print(f"  {pkgs} package(s) upgraded across {workspaces_total} workspace(s)")

    packages_behind = summary.get("packages_behind_latest", 0)
    actions_behind = summary.get("actions_behind_latest", 0)
    total_behind = packages_behind + actions_behind
    if total_behind > 0:
        parts: list[str] = []
        if packages_behind > 0:
            by_manager: dict[str, int] = summary.get("packages_behind_latest_by_manager", {})
            if by_manager:
                # One line per manager so the remediation advice fits each.
                for manager in sorted(by_manager):
                    count = by_manager[manager]
                    manager_hint = _behind_latest_hint(manager)
                    parts.append(f"{count} {manager} package(s) behind latest ({manager_hint})")
            else:
                parts.append(f"{packages_behind} package(s) behind latest ({_LATEST_HINT_DEFAULT})")
        if actions_behind > 0:
            parts.append(
                f"{actions_behind} action(s) have a newer major available"
                " (report-only when no floating major tag exists upstream)"
            )
        print()
        for part in parts:
            print(f"  behind-latest: {part}")

    # One-line staleness roll-up: top-3 repos by behind-count x days-behind.
    # ``staleness_top`` is only present in the summary when some repo scores
    # above zero, so an empty state store keeps the footer byte-identical.
    staleness_top = summary.get("staleness_top") or []
    if staleness_top:
        rendered = ", ".join(f"{item['repo']} {item['score']}" for item in staleness_top)
        print()
        print(f"  staleness: {rendered} (behind-count x days-behind)")

    if failures:
        print()
        print("Failures:")
        shown = failures[:_MAX_FAILURE_LINES]
        for label, reason in shown:
            print(f"  {label}: {reason}")
        remaining = len(failures) - len(shown)
        if remaining > 0:
            print(f"  ... and {remaining} more")

    if discovery_skips:
        # Informational only — these manifests carry nothing to upgrade (no
        # lockfile / no package manager / outside a git repo). They are NOT
        # failures or warnings; keep them visually distinct from the Failures
        # block above so a clean run never reads as if it had problems.
        print()
        print("Skipped (not actionable — informational, no action needed):")
        shown_skips = discovery_skips[:_MAX_FAILURE_LINES]
        for entry in shown_skips:
            eco = entry.get("ecosystem", "?")
            print(f"  [{eco}] {entry.get('path', '')} — {entry.get('reason', '')}")
        remaining_skips = len(discovery_skips) - len(shown_skips)
        if remaining_skips > 0:
            print(f"  ... and {remaining_skips} more")

    print()
    print(f"failures: {summary['failure_count']}")
    print(f"exit_code: {exit_code}")
