"""Upgrade plan/apply orchestration (shared by console entry points)."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from repo_ops.core.cli import (
    build_upgrade_parser,
    reject_legacy_upgrade_flags,
    wire_verbose,
)
from repo_ops.core.constants import DEFAULT_ROOTS
from repo_ops.core.parallel import run_bounded_pool
from repo_ops.upgrade import actions, orchestration, report
from repo_ops.upgrade import workspaces as workspace_discovery
from repo_ops.upgrade.models import RepoBundle, Workspace, WorkspaceResult

_FAIL_FAST = orchestration.FAIL_FAST_EVENT


def _has_actionable_work(bundles: dict[Path, RepoBundle]) -> bool:
    for bundle in bundles.values():
        for wr in bundle.workspaces:
            if wr.changes or wr.remediations or wr.status not in ("ok", "skip"):
                return True
        for ab in bundle.actions:
            if ab.status != "ok":
                return True
            if ab.detail and "SHA-pinned" in (ab.detail or ""):
                continue
            if ab.pin != ab.latest:
                return True
    return False


def run_upgrade(*, apply: bool, argv: list[str] | None = None) -> int:
    reject_legacy_upgrade_flags(argv)
    parser = build_upgrade_parser(apply=apply)
    args = parser.parse_args(argv)
    wire_verbose(args)

    roots = [str(Path(r).expanduser()) for r in (args.roots or DEFAULT_ROOTS)]
    skip_remediations = set()
    if apply:
        skip_remediations = {
            s.strip() for s in args.skip_remediations.split(",") if s.strip()
        }

    found: list[Workspace] = workspace_discovery.discover_all(roots, flat=args.flat)
    found = orchestration.filter_workspaces(found, args.eco_filter)

    bundles = workspace_discovery.group_by_repo(found, roots)

    _FAIL_FAST.clear()

    def _process_one(wr: WorkspaceResult) -> WorkspaceResult:
        return orchestration.process_workspace(
            wr,
            do_apply=apply,
            do_verify=apply and not args.no_verify,
            verify_test=apply and args.verify_test,
            skip_remediations=skip_remediations,
            allow_dirty=args.allow_dirty,
        )

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
        pending_bundles = [bundle for bundle in bundles.values() if bundle.workspaces]

        def _repo_failed(batch: list[WorkspaceResult]) -> bool:
            if args.fail_fast and any(r.status == "fail" for r in batch):
                _FAIL_FAST.set()
                return True
            return False

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

    for bundle in bundles.values():
        path_to_result = {r.workspace.path: r for r in all_results}
        bundle.workspaces = [
            path_to_result.get(wr.workspace.path, wr) for wr in bundle.workspaces
        ]

    if not args.skip_actions:
        for bundle in bundles.values():
            if apply and bundle.any_fail():
                continue
            bundle.actions = actions.scan_actions(bundle.git_repo, apply=apply)

    only_outdated = getattr(args, "only_outdated", False)
    if only_outdated:
        for bundle in bundles.values():
            bundle.workspaces = [
                wr
                for wr in bundle.workspaces
                if wr.changes or wr.status != "ok" or wr.remediations
            ]

    json_out = args.json_out
    if json_out:
        report.print_json_report(
            bundles,
            roots=roots,
            mode="apply" if apply else "plan",
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

    any_fail = any(
        wr.status == "fail" for b in bundles.values() for wr in b.workspaces
    ) or any(a.status == "fail" for b in bundles.values() for a in b.actions)
    if any_fail:
        return 1
    if not apply and _has_actionable_work(bundles):
        return 1
    return 0


def main_plan(argv: list[str] | None = None) -> int:
    return run_upgrade(apply=False, argv=argv)


def main_apply(argv: list[str] | None = None) -> int:
    return run_upgrade(apply=True, argv=argv)
