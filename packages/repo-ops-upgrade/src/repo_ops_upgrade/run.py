"""Upgrade plan/apply orchestration (shared by console entry points)."""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
from typing import Literal

from repo_ops_core.cli import (
    build_upgrade_parser,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.parallel import run_bounded_pool
from repo_ops_core.tee import tee_stdout_stderr
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult

from repo_ops_upgrade import (
    actions,
    orchestration,
    post_apply_commit,
    report,
    submodules,
)
from repo_ops_upgrade import discovery as workspace_discovery
from repo_ops_upgrade.discovery import DiscoveryDiagnostics

_FAIL_FAST = orchestration.FAIL_FAST_EVENT


def _has_actionable_work(bundles: dict[Path, RepoBundle]) -> bool:
    for bundle in bundles.values():
        for wr in bundle.workspaces:
            if wr.changes or wr.remediations or wr.status not in ("ok", "skip"):
                return True
        for ab in bundle.actions:
            if ab.status != "ok":
                return True
            if "report only" in (ab.detail or "").lower():
                continue
            if ab.pin != ab.latest:
                return True
    return False


def _resolve_exit_code(
    bundles: dict[Path, RepoBundle],
    *,
    apply: bool,
    strict_exit: bool,
    has_actionable_work: bool,
) -> int:
    if strict_exit and report.any_workspace_skip_or_fail(bundles):
        return 1
    if report.count_failures(bundles) > 0:
        return 1
    if not apply and has_actionable_work:
        return 1
    return 0


def _run_upgrade_body(
    *,
    apply: bool,
    args,
    roots: list[str],
    follow_symlinks: bool = False,
) -> int:
    # --latest / --go-upgrade-mode set the per-ecosystem env vars the checker
    # modules already read, BEFORE any worker pool starts (#102). Additive: the
    # env-var reads stay; these flags just populate them for this process.
    if getattr(args, "latest", False):
        os.environ["REPO_OPS_BUN_UPDATE_LATEST"] = "1"
        os.environ["REPO_OPS_PNPM_UPDATE_LATEST"] = "1"
        os.environ["REPO_OPS_YARN_UPDATE_LATEST"] = "1"
        os.environ.setdefault("REPO_OPS_GO_UPGRADE_MODE", "explicit")
    go_mode = getattr(args, "go_upgrade_mode", None)
    if go_mode:
        os.environ["REPO_OPS_GO_UPGRADE_MODE"] = go_mode

    skip_remediations = set()
    if apply:
        skip_remediations = {
            s.strip() for s in args.skip_remediations.split(",") if s.strip()
        }

    use_json = args.json
    json_report_path = getattr(args, "json_report_path", None)
    # Always collect discovery diagnostics so found-but-unactionable manifests are
    # counted (and surfaced) in human runs too, not only under --json (#24).
    diagnostics = DiscoveryDiagnostics()
    found: list[Workspace] = workspace_discovery.discover_all(
        roots,
        flat=args.flat,
        diagnostics=diagnostics,
        follow_symlinks=follow_symlinks,
    )
    found = orchestration.filter_workspaces(found, args.eco_filter)

    bundles = workspace_discovery.group_by_repo(found, roots)
    if not args.skip_actions:
        bundles = workspace_discovery.merge_action_only_bundles(
            bundles,
            roots,
            follow_symlinks=follow_symlinks,
        )

    include_submodules = getattr(args, "submodules", False)
    submodule_order = getattr(args, "submodule_order", "discovery")
    need_submodule_scan = include_submodules or (
        apply and submodule_order == "children-first"
    )
    submodule_links: list[submodules.SubmoduleLink] = []
    submodules_json: list[dict[str, str]] | None = None
    if need_submodule_scan:
        submodule_links = submodules.discover_submodules_under_roots(
            roots,
            flat=args.flat,
        )
        if include_submodules:
            submodules_json = submodules.submodules_to_json(submodule_links)

    bundle_order = list(bundles.keys())
    if apply and submodule_order == "children-first" and submodule_links:
        bundle_order = submodules.sort_bundles_children_first(
            bundle_order,
            submodule_links,
        )

    _FAIL_FAST.clear()

    def _process_one(wr: WorkspaceResult) -> WorkspaceResult:
        # Guard the worker body: a raising worker must surface as a FAILED
        # workspace, not get silently dropped from results and reported clean
        # with exit 0 (#62). Mutate and return the input result so its
        # workspace.path still matches in reconciliation below.
        try:
            return orchestration.process_workspace(
                wr,
                do_apply=apply,
                do_verify=apply and not args.no_verify,
                verify_test=apply and args.verify_test,
                skip_remediations=skip_remediations,
                allow_dirty=args.allow_dirty,
                on_verify_fail=getattr(args, "on_verify_fail", "keep"),
            )
        except Exception as exc:
            wr.status = "fail"
            wr.error = str(exc)
            return wr

    def _process_repo(bundle: RepoBundle) -> list[WorkspaceResult]:
        results: list[WorkspaceResult] = []
        for wr in bundle.workspaces:
            updated = _process_one(wr)
            results.append(updated)
            if args.fail_fast and updated.status == "fail":
                _FAIL_FAST.set()
                break
        return results

    all_results: list[WorkspaceResult] = []
    if apply:
        ordered_bundles = [
            bundles[key]
            for key in bundle_order
            if key in bundles and bundles[key].workspaces
        ]
        pending_bundles = ordered_bundles

        def _repo_failed(batch: list[WorkspaceResult]) -> bool:
            if args.fail_fast and any(r.status == "fail" for r in batch):
                _FAIL_FAST.set()
                return True
            return False

        children_first = submodule_order == "children-first" and bool(submodule_links)
        if children_first:
            for bundle in pending_bundles:
                if _FAIL_FAST.is_set():
                    break
                batch = _process_repo(bundle)
                all_results.extend(batch)
                if _repo_failed(batch):
                    break
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for batch in run_bounded_pool(
                    pool,
                    pending_bundles,
                    _process_repo,
                    max_in_flight=args.jobs,
                    should_stop=_FAIL_FAST.is_set,
                    stop_on_result=_repo_failed,
                ):
                    all_results.extend(batch)
    else:
        all_wrs = [wr for bundle in bundles.values() for wr in bundle.workspaces]

        def _workspace_failed(wr: WorkspaceResult) -> bool:
            if args.fail_fast and wr.status == "fail":
                _FAIL_FAST.set()
                return True
            return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            all_results = run_bounded_pool(
                pool,
                all_wrs,
                _process_one,
                max_in_flight=args.jobs,
                should_stop=_FAIL_FAST.is_set,
                stop_on_result=_workspace_failed,
            )

    path_to_result = {r.workspace.path: r for r in all_results}
    for bundle in bundles.values():
        updated: list[WorkspaceResult] = []
        for wr in bundle.workspaces:
            if wr.workspace.path in path_to_result:
                updated.append(path_to_result[wr.workspace.path])
            elif _FAIL_FAST.is_set():
                wr.status = "skip"
                wr.error = "fail-fast"
                updated.append(wr)
            else:
                # No result and not fail-fast: the worker crashed and dropped
                # this workspace. Mark it failed rather than re-appending the
                # original (default-status) result as a silent clean pass (#62).
                wr.status = "fail"
                wr.error = "worker crashed (no result returned)"
                updated.append(wr)
        bundle.workspaces = updated

    if not args.skip_actions:
        # SHA pins are preserved by default (supply-chain hardening).
        # Only unpin when the user explicitly opts in via --actions-unpin-sha.
        keep_sha = not bool(getattr(args, "actions_unpin_sha", False))
        _raw_tag_style = getattr(args, "actions_tag_style", "major")
        tag_style: actions.TagStyle = "exact" if _raw_tag_style == "exact" else "major"
        # One shared cache for the whole run so each (action, ref) is resolved
        # at most once across all repos — avoids redundant GitHub API calls.
        run_tag_cache: dict[str, dict | None] = {}
        for bundle in bundles.values():
            if apply and (bundle.any_fail() or bundle.any_dirty_skip()):
                continue
            bundle.actions, bundle.actions_workflows_scanned = actions.scan_actions(
                bundle.git_repo,
                apply=apply,
                keep_sha=keep_sha,
                tag_style=tag_style,
                tag_cache=run_tag_cache,
            )

    only_outdated = getattr(args, "only_outdated", False)
    if only_outdated:
        for bundle in bundles.values():
            bundle.workspaces = [
                wr
                for wr in bundle.workspaces
                if wr.changes
                or wr.behind_latest
                or wr.status != "ok"
                or wr.remediations
            ]

    pointer_hints: list[dict[str, str]] | None = None
    if submodule_links and (apply or include_submodules):
        pointer_hints = submodules.parent_repos_needing_pointer_commit(
            submodule_links,
        )
        if not pointer_hints:
            pointer_hints = None

    json_submodules = submodules_json if include_submodules else None
    json_pointer_hints = pointer_hints if (apply and pointer_hints) else None
    if include_submodules and pointer_hints and not apply:
        json_pointer_hints = pointer_hints

    if use_json:
        mode: Literal["plan", "apply"] = "apply" if apply else "plan"
        compact = bool(getattr(args, "compact", False))
        if json_report_path:
            written = report.write_json_report(
                Path(json_report_path),
                bundles,
                roots=roots,
                mode=mode,
                diagnostics=diagnostics,
                submodules=json_submodules,
                submodule_pointer_hints=json_pointer_hints,
                compact=compact,
            )
            print(written)
        else:
            report.print_json_report(
                bundles,
                roots=roots,
                mode=mode,
                diagnostics=diagnostics,
                submodules=json_submodules,
                submodule_pointer_hints=json_pointer_hints,
                compact=compact,
            )
    elif not args.quiet:
        top_n = getattr(args, "top", 10)
        report.print_human_report(
            bundles,
            dry_run=not apply,
            only_outdated=only_outdated,
            top_n=top_n,
            include_actions=not args.skip_actions,
        )
        if include_submodules and submodules_json:
            report.print_submodule_section(
                submodules_json,
                pointer_hints=pointer_hints,
            )

    exit_code = _resolve_exit_code(
        bundles,
        apply=apply,
        strict_exit=args.strict_exit,
        has_actionable_work=_has_actionable_work(bundles),
    )
    if apply:
        commit_mode: Literal["none", "conventional", "agent"] = getattr(
            args, "commit", "none"
        )
        if commit_mode and commit_mode != "none":
            _, commit_exit = post_apply_commit.run_post_apply_commits(
                bundles,
                mode=commit_mode,
                dry_run=getattr(args, "commit_dry_run", False),
                allow_dirty=args.allow_dirty,
                commit_on_fail=getattr(args, "commit_on_fail", False),
                quiet=args.quiet,
            )
            if commit_exit != 0:
                exit_code = 1 if exit_code == 0 else exit_code

    if not use_json:
        footer_hints = pointer_hints if apply else None
        if include_submodules and pointer_hints and not apply:
            footer_hints = pointer_hints
        report.print_run_footer(
            summary=report.build_run_summary(bundles, diagnostics=diagnostics),
            failures=report.collect_run_failures(bundles),
            exit_code=exit_code,
            max_failures=args.max_failures,
            show_skips=args.strict_exit,
            submodule_pointer_hints=footer_hints,
            discovery_skips=diagnostics.skipped,
        )
    return exit_code


def run_upgrade(*, apply: bool, argv: list[str] | None = None) -> int:
    parser = build_upgrade_parser(apply=apply)
    args = parser.parse_args(argv)
    if getattr(args, "json_report_path", None) and not args.json:
        parser.error("--json-out requires --json")
    wire_verbose(args)

    roots = resolve_scan_roots(list(args.roots or DEFAULT_ROOTS), parser)
    follow_symlinks = bool(getattr(args, "follow_symlinks", False))
    if args.log_file:
        with tee_stdout_stderr(Path(args.log_file).expanduser()):
            return _run_upgrade_body(
                apply=apply,
                args=args,
                roots=roots,
                follow_symlinks=follow_symlinks,
            )
    return _run_upgrade_body(
        apply=apply,
        args=args,
        roots=roots,
        follow_symlinks=follow_symlinks,
    )


def main_plan(argv: list[str] | None = None) -> int:
    return run_upgrade(apply=False, argv=argv)


def main_apply(argv: list[str] | None = None) -> int:
    return run_upgrade(apply=True, argv=argv)
