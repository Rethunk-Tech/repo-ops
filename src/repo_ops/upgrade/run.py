"""Upgrade plan/apply orchestration (shared by console entry points)."""

from __future__ import annotations

import concurrent.futures
import json
from collections.abc import Callable
from pathlib import Path

from repo_ops.core.cli import wire_verbose
from repo_ops.core.constants import DEFAULT_ROOTS
from repo_ops.core.process import set_verbose
from repo_ops.upgrade import engine
from repo_ops.upgrade.cli_args import build_upgrade_parser, reject_legacy_flags
from repo_ops.upgrade.models import RepoBundle, Workspace, WorkspaceResult

_FAIL_FAST_EVENT = engine._FAIL_FAST_EVENT


def _run_plan_parallel(
    pool: concurrent.futures.ThreadPoolExecutor,
    all_wrs: list[WorkspaceResult],
    process_one: Callable[[WorkspaceResult], WorkspaceResult],
    *,
    jobs: int,
    fail_fast: bool,
) -> list[WorkspaceResult]:
    all_results: list[WorkspaceResult] = []
    pending_wrs: list[WorkspaceResult] = list(all_wrs)
    wr_futures: dict[
        concurrent.futures.Future[WorkspaceResult], WorkspaceResult
    ] = {}
    while pending_wrs or wr_futures:
        while (
            pending_wrs
            and len(wr_futures) < jobs
            and not _FAIL_FAST_EVENT.is_set()
        ):
            wr = pending_wrs.pop(0)
            fut = pool.submit(process_one, wr)
            wr_futures[fut] = wr
        if not wr_futures:
            break
        done, _ = concurrent.futures.wait(
            wr_futures.keys(),
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        for fut in done:
            wr_futures.pop(fut)
            updated = fut.result()
            all_results.append(updated)
            if fail_fast and updated.status == "fail":
                _FAIL_FAST_EVENT.set()
                for pending_fut in list(wr_futures):
                    pending_fut.cancel()
                wr_futures.clear()
                pending_wrs.clear()
    return all_results


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
    reject_legacy_flags(argv)
    parser = build_upgrade_parser(apply=apply)
    args = parser.parse_args(argv)
    wire_verbose(args)

    roots = [str(Path(r).expanduser()) for r in (args.roots or DEFAULT_ROOTS)]
    skip_remediations = set()
    if apply:
        skip_remediations = {
            s.strip() for s in args.skip_remediations.split(",") if s.strip()
        }

    workspaces: list[Workspace] = engine.discover_all(roots, flat=args.flat)
    workspaces = engine.filter_workspaces(workspaces, args.eco_filter)

    bundles = engine.group_by_repo(workspaces, roots)

    set_verbose(args.verbose)
    _FAIL_FAST_EVENT.clear()

    def _process_one(wr: WorkspaceResult) -> WorkspaceResult:
        return engine.process_workspace(
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
                _FAIL_FAST_EVENT.set()
                break
        return results

    all_results: list[WorkspaceResult] = []
    if apply:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            pending_bundles: list[RepoBundle] = [
                bundle for bundle in bundles.values() if bundle.workspaces
            ]
            futures: dict[
                concurrent.futures.Future[list[WorkspaceResult]], RepoBundle
            ] = {}
            while pending_bundles or futures:
                while (
                    pending_bundles
                    and len(futures) < args.jobs
                    and not _FAIL_FAST_EVENT.is_set()
                ):
                    bundle = pending_bundles.pop(0)
                    fut = pool.submit(_process_repo, bundle)
                    futures[fut] = bundle
                if not futures:
                    break
                done, _ = concurrent.futures.wait(
                    futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for fut in done:
                    futures.pop(fut)
                    updated = fut.result()
                    all_results.extend(updated)
                    if args.fail_fast and any(r.status == "fail" for r in updated):
                        _FAIL_FAST_EVENT.set()
                        for pending_fut in list(futures):
                            pending_fut.cancel()
                        futures.clear()
                        pending_bundles.clear()
    else:
        all_wrs = [wr for bundle in bundles.values() for wr in bundle.workspaces]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            all_results = _run_plan_parallel(
                pool,
                all_wrs,
                _process_one,
                jobs=args.jobs,
                fail_fast=args.fail_fast,
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
            bundle.actions = engine.scan_actions(bundle.git_repo, apply=apply)

    only_outdated = getattr(args, "only_outdated", False)
    if only_outdated:
        for bundle in bundles.values():
            bundle.workspaces = [
                wr
                for wr in bundle.workspaces
                if wr.changes or wr.status != "ok" or wr.remediations
            ]

    json_out = getattr(args, "json_out", False) or getattr(args, "json", False)
    if json_out:
        payload = {
            "roots": roots,
            "mode": "apply" if apply else "plan",
            "repos": [
                b.to_json() for b in sorted(bundles.values(), key=lambda x: x.label)
            ],
        }
        print(json.dumps(payload, indent=2))
    elif not args.quiet:
        top_n = getattr(args, "top", 10)
        engine.print_human_report(
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
