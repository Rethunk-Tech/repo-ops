"""Upgrade plan/apply orchestration (shared by console entry points)."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import os
import sys
import threading
from itertools import groupby
from pathlib import Path

from repo_ops_core.cli import (
    build_upgrade_parser,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.config import (
    load_global_config,
    load_repo_config,
)
from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.exclude import (
    filter_repo_paths,
    merge_exclude_patterns,
    workspace_matches_exclude,
)
from repo_ops_core.filesystem import new_run_dir, write_json_atomic
from repo_ops_core.git import is_git_dirty
from repo_ops_core.parallel import run_bounded_pool
from repo_ops_core.tee import tee_stdout_stderr
from repo_ops_models import ActionBump, RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade import (
    actions,
    orchestration,
    post_apply_commit,
    report,
    submodules,
)
from repo_ops_upgrade import discovery as workspace_discovery
from repo_ops_upgrade.discovery import DiscoveryDiagnostics
from repo_ops_upgrade.post_apply import (
    capture_worktree_state,
    note_foreign_changes,
    note_retry_skipped,
    reinstall_from_lockfile,
    rollback_owned_edits,
    tree_diff,
)


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
    has_actionable_work: bool,
) -> int:
    if report.count_failures(bundles) > 0:
        return 1
    if not apply and has_actionable_work:
        return 1
    return 0


def _rollback_vetoed_repo(
    repo: Path,
    repo_before: dict[str, bytes | None] | None,
    results: list[WorkspaceResult],
) -> None:
    """Undo the passing siblings of a failed workspace, touching only their own edits."""
    tree_ok = repo_before is not None
    for wr in results:
        # A verify-failed workspace already reverted and reinstalled itself.
        if wr.status == "skip" or wr.verify_fail_disposition is not None:
            continue
        tree_ok = rollback_owned_edits(repo, wr.owned_edits) and tree_ok
    foreign = tree_diff(repo, repo_before) if repo_before is not None else None
    if foreign is None:
        tree_ok = False
    for wr in results:
        if wr.status == "skip" or wr.verify_fail_disposition is not None:
            continue
        installed_ok = reinstall_from_lockfile(wr.workspace)
        if wr.status == "ok":
            wr.status = "skip"
            wr.error = "reverted: another workspace in this repo failed"
        if foreign:
            wr.error = note_foreign_changes(wr.error, foreign)
        wr.reverted_ok = wr.reverted_ok and tree_ok and installed_ok and not foreign


def _cli_flag(args: object, name: str) -> bool:
    """Return True only for an explicit True CLI flag.

    ``bool(getattr(...))`` is unsafe with bare ``MagicMock`` doubles: missing
    attributes auto-create truthy mocks. Identity ``is True`` matches argparse
    ``store_true`` and rejects mocks.
    """
    return getattr(args, name, False) is True


def _run_upgrade_body(
    *,
    apply: bool,
    args: argparse.Namespace,
    roots: list[str],
    fleet: bool = False,
    report_path: Path | None = None,
) -> int:
    # Apply commits each verified repo unless told not to; an unverified apply
    # never commits.
    commit = apply and not _cli_flag(args, "no_commit") and not _cli_flag(args, "no_verify")
    if apply and _cli_flag(args, "no_verify") and not _cli_flag(args, "no_commit"):
        print("[commit skip]: verify was skipped so commit was skipped")
    # A fleet sweep (no roots named) leaves dirty repos alone and reverts a
    # failed repo so the retry below starts clean. Named roots are checkouts the
    # operator is working in: they are processed even when dirty, and a failed
    # verify keeps the changes for inspection.
    allow_dirty = not fleet
    on_verify_fail = "revert" if fleet else "keep"
    diagnostics = DiscoveryDiagnostics()
    found: list[Workspace] = workspace_discovery.discover_all(roots, diagnostics=diagnostics)
    found = orchestration.filter_workspaces(found, args.eco_filter, None)

    bundles = workspace_discovery.group_by_repo(found, roots)
    if args.eco_filter in (None, "actions"):
        bundles = workspace_discovery.merge_action_only_bundles(bundles, roots)

    _repo_cfgs = {bundle.git_repo: load_repo_config(bundle.git_repo) for bundle in bundles.values()}
    exclude_patterns: list[str] = getattr(args, "exclude_patterns", None) or []
    kept_paths: list[Path] = []
    excluded_count = 0
    excluded_repos: list[Path] = []
    # Exclude filters what discovery found, never a checkout named on the CLI.
    named_roots = set() if fleet else {Path(r).resolve() for r in roots}
    for path, bundle in bundles.items():
        if bundle.git_repo.resolve() in named_roots:
            kept_paths.append(path)
            continue
        repo_exclude = _repo_cfgs.get(bundle.git_repo, {}).get("exclude")
        bundle_exclude = merge_exclude_patterns(exclude_patterns, repo_exclude)
        bundle_kept, bundle_excluded = filter_repo_paths(
            [path],
            bundle_exclude,
            label_fn=lambda candidate: bundles[candidate].label,
        )
        kept_paths.extend(bundle_kept)
        excluded_count += bundle_excluded
        if bundle_excluded:
            excluded_repos.append(bundle.git_repo)
    bundles = {path: bundles[path] for path in kept_paths}
    diagnostics.drop_under(excluded_repos)
    for bundle in bundles.values():
        repo_exclude = _repo_cfgs.get(bundle.git_repo, {}).get("exclude")
        if isinstance(repo_exclude, str):
            repo_exclude = [repo_exclude]
        if repo_exclude:
            bundle.workspaces = [
                w
                for w in bundle.workspaces
                if not workspace_matches_exclude(bundle.git_repo, w.workspace.path, repo_exclude)
            ]

    submodule_links = submodules.discover_submodules_under_roots(roots)
    submodules_json = submodules.submodules_to_json(submodule_links)

    bundle_order = list(bundles.keys())
    if apply and submodule_links:
        bundle_order = submodules.sort_bundles_children_first(
            bundle_order,
            submodule_links,
        )

    def _process_one(
        wr: WorkspaceResult, *, repo_started_dirty: bool | None = None
    ) -> WorkspaceResult:
        # Guard the worker body: a raising worker must surface as a FAILED
        # workspace, not get silently dropped from results and reported clean
        # with exit 0. Mutate and return the input result so its
        # workspace.path still matches in reconciliation below.
        try:
            return orchestration.process_workspace(
                wr,
                do_apply=apply,
                do_verify=apply and not _cli_flag(args, "no_verify"),
                allow_dirty=allow_dirty,
                on_verify_fail=on_verify_fail,
                repo_started_dirty=repo_started_dirty,
            )
        except Exception as exc:
            wr.status = "fail"
            wr.error = str(exc)
            return wr

    # One failed workspace vetoes its repo's commit, so under revert the passing
    # siblings roll back too; otherwise their edits stay behind uncommitted and
    # the next sweep skips the repo as dirty.
    rollback_on_veto = commit and fleet

    def _process_repo(bundle: RepoBundle) -> list[WorkspaceResult]:
        results: list[WorkspaceResult] = []
        # Dirtiness is judged right before each workspace, not once per bundle: a
        # concurrent writer can touch the checkout at any point of a long sweep,
        # and a rollback after that would otherwise sit on top of their edits.
        # Siblings' own applied-but-uncommitted edits are expected, so after the
        # first workspace the tree is compared with its state after the last one.
        repo_before = capture_worktree_state(bundle.git_repo) if rollback_on_veto else None
        expected: dict[str, bytes | None] | None = None
        started_dirty = is_git_dirty(bundle.git_repo) if apply and allow_dirty else None
        for wr in bundle.workspaces:
            dirty = started_dirty
            if apply and not allow_dirty:
                if expected is None:
                    dirty = is_git_dirty(bundle.git_repo)
                else:
                    dirty = capture_worktree_state(bundle.git_repo) != expected
            updated = _process_one(wr, repo_started_dirty=dirty)
            results.append(updated)
            if apply and not allow_dirty:
                expected = capture_worktree_state(bundle.git_repo)
        if rollback_on_veto and any(r.status == "fail" for r in results):
            _rollback_vetoed_repo(bundle.git_repo, repo_before, results)
        return results

    all_results: list[WorkspaceResult] = []
    if apply:
        ordered_bundles = [
            bundles[key] for key in bundle_order if key in bundles and bundles[key].workspaces
        ]
        pending_bundles = ordered_bundles

        if submodule_links:
            # Build a depth map: children have depth 0, parents have depth > 0.
            # Bundles at the same depth are independent and can run in parallel.
            pending_keys = [
                key for key in bundle_order if key in bundles and bundles[key].workspaces
            ]
            key_set_p = set(pending_keys)
            depth_map: dict[Path, int] = dict.fromkeys(pending_keys, 0)
            budget = len(pending_keys)
            _changed = True
            while _changed and budget > 0:
                budget -= 1
                _changed = False
                for link in submodule_links:
                    if link.git_repo not in key_set_p or link.parent not in key_set_p:
                        continue
                    new_d = depth_map[link.git_repo] + 1
                    if new_d > depth_map[link.parent]:
                        depth_map[link.parent] = new_d
                        _changed = True
            sorted_keys = sorted(pending_keys, key=lambda k: depth_map.get(k, 0))
            for _level, level_keys_iter in groupby(sorted_keys, key=lambda k: depth_map.get(k, 0)):
                level_bundles = [bundles[k] for k in level_keys_iter]
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as _level_pool:
                    for batch in run_bounded_pool(
                        _level_pool, level_bundles, _process_repo, max_in_flight=args.jobs
                    ):
                        all_results.extend(batch)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for batch in run_bounded_pool(
                    pool, pending_bundles, _process_repo, max_in_flight=args.jobs
                ):
                    all_results.extend(batch)
    else:
        all_wrs = [wr for bundle in bundles.values() for wr in bundle.workspaces]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            all_results = run_bounded_pool(pool, all_wrs, _process_one, max_in_flight=args.jobs)

    # Retry each failed repo once, alone, after the parallel sweep. Rollback left
    # it at its pre-apply state, so the retry is a clean re-apply. Failures that
    # only exist under fleet-wide concurrency (a resolver blip in govulncheck,
    # golangci-lint's machine-wide runner lock, a test timeout under load) failed
    # 9 of 9 repos in one sweep, and all 9 passed when re-run one at a time.
    if rollback_on_veto:
        failed_repos = sorted({r.workspace.git_repo for r in all_results if r.status == "fail"})
        if failed_repos:
            print(f"retrying {len(failed_repos)} failed repo(s) one at a time")
            retry_set = set(failed_repos)
            failed_results = {
                key: [r for r in all_results if r.workspace.git_repo == key] for key in failed_repos
            }
            all_results = [r for r in all_results if r.workspace.git_repo not in retry_set]
            for key in failed_repos:
                bundle = bundles[key]
                if is_git_dirty(bundle.git_repo):
                    # Rollback left foreign edits, or someone started working
                    # here since: a re-apply would sit on top of them.
                    for r in failed_results[key]:
                        r.error = note_retry_skipped(r.error)
                    all_results.extend(failed_results[key])
                    continue
                bundle.workspaces = [
                    WorkspaceResult(workspace=wr.workspace) for wr in bundle.workspaces
                ]
                all_results.extend(_process_repo(bundle))

    # Key by (path, manager): a single directory can host multiple ecosystems
    # (e.g. npm + container). Path-only keys let the last worker overwrite the
    # earlier result, dropping non-container workspaces from the report.
    path_to_result = {(r.workspace.path, r.workspace.manager): r for r in all_results}
    for bundle in bundles.values():
        updated: list[WorkspaceResult] = []
        for wr in bundle.workspaces:
            result_key = (wr.workspace.path, wr.workspace.manager)
            if result_key in path_to_result:
                updated.append(path_to_result[result_key])
            else:
                # No result: the worker crashed and dropped
                # this workspace. Mark it failed rather than re-appending the
                # original (default-status) result as a silent clean pass.
                wr.status = "fail"
                wr.error = "worker crashed (no result returned)"
                updated.append(wr)
        bundle.workspaces = updated

    # One shared cache for the whole run so each (action, ref) is resolved
    # at most once across all repos — avoids redundant GitHub API calls.
    # The cache is shared across threads; we copy-in before each slow API
    # call and merge-in after under lock to avoid dict mutation races.
    run_tag_cache: dict[str, dict | None] = {}
    if args.eco_filter in (None, "actions"):
        actions.load_tag_cache_into(run_tag_cache)
        _tag_cache_lock = threading.Lock()

        def _scan_one_bundle_actions(bundle: RepoBundle) -> None:
            # Fleet dirty-skip leaves the checkout untouched; do not apply
            # workflow pins on top of someone else's tree. A workspace fail
            # (including named-root keep-on-verify-fail) still scans Actions.
            if apply and bundle.any_dirty_skip():
                return
            local_cache: dict[str, dict | None] = {}
            with _tag_cache_lock:
                local_cache.update(run_tag_cache)
            repo_exclude = _repo_cfgs.get(bundle.git_repo, {}).get("exclude")
            if isinstance(repo_exclude, str):
                repo_exclude = [repo_exclude]
            try:
                bumps, count = actions.scan_actions(
                    bundle.git_repo,
                    apply=apply,
                    tag_cache=local_cache,
                    exclude_patterns=repo_exclude or None,
                )
            except Exception as exc:
                # Isolate: a non-WorkflowParseError (e.g. OSError writing a
                # workflow) must surface as a FAILED actions entry rather than
                # propagating through the pool and tearing down the whole run
                # AFTER lockfiles were already mutated on disk (#TASK-2).
                bundle.actions = [
                    ActionBump(
                        workflow="(error)",
                        action="(worker)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=str(exc),
                    )
                ]
                bundle.actions_workflows_scanned = 0
                return
            with _tag_cache_lock:
                run_tag_cache.update(local_cache)
            bundle.actions = bumps
            bundle.actions_workflows_scanned = count

        # One batched lookup for every action the fleet references, before the pool
        # asks for them one at a time. Only repo names are needed here, so the
        # workflows are skimmed rather than parsed; scan_actions parses them
        # properly a moment later and a name this misses just takes the REST path.
        _all_uses: list[str] = []
        for _bundle in bundles.values():
            for _wf in actions.discover_workflow_files(
                _bundle.git_repo
            ) + actions.discover_action_files(_bundle.git_repo):
                with contextlib.suppress(OSError):
                    _all_uses.extend(actions.skim_workflow_uses(_wf))
        if _all_uses:
            actions.prefetch_tag_info(_all_uses, run_tag_cache)

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as _actions_pool:
            list(_actions_pool.map(_scan_one_bundle_actions, list(bundles.values())))
        actions.save_tag_cache_from(run_tag_cache)

    # Submodule tag advances (apply-only).
    # Runs BEFORE pointer_hints so that staged gitlinks are visible to the
    # hint scan and are committed by the post-apply commit phase.
    submodule_gitlink_map: dict[Path, set[str]] = {}
    if apply and submodule_links and args.eco_filter in (None, "submodules"):
        _sub_bumps = submodules.apply_submodule_tag_advances(
            submodule_links,
            allow_dirty=allow_dirty,
        )
        for _bump in _sub_bumps:
            if _bump.is_actionable:
                submodule_gitlink_map.setdefault(_bump.parent, set()).add(_bump.path)
                continue
            if "fetch failed" not in _bump.latest_tag and "checkout failed" not in _bump.latest_tag:
                continue
            for bundle in bundles.values():
                if bundle.git_repo.resolve() != _bump.parent.resolve():
                    continue
                bundle.actions.append(
                    ActionBump(
                        workflow=_bump.path,
                        action="git submodule",
                        pin=_bump.current_tag,
                        latest="",
                        status="fail",
                        detail=_bump.latest_tag,
                    )
                )
                break

    pointer_hints = submodules.parent_repos_needing_pointer_commit(submodule_links) or None

    exit_code = _resolve_exit_code(
        bundles, apply=apply, has_actionable_work=_has_actionable_work(bundles)
    )

    report.print_human_report(bundles, dry_run=not apply)
    if submodules_json:
        report.print_submodule_section(submodules_json, pointer_hints=pointer_hints)

    if commit:
        child_outcomes, commit_exit = post_apply_commit.run_post_apply_commits(
            bundles,
            max_workers=args.jobs,
            submodule_gitlink_map=submodule_gitlink_map if submodule_gitlink_map else None,
        )
        if commit_exit != 0:
            exit_code = 1 if exit_code == 0 else exit_code
        # Umbrella pointer-bump commits: after all child commits complete,
        # commit dirty gitlinks in umbrella repos for children that received
        # commits this run.
        if submodule_links:
            _, umbrella_exit = post_apply_commit.commit_umbrella_submodule_pointers(
                submodule_links, child_outcomes
            )
            if umbrella_exit != 0:
                exit_code = 1 if exit_code == 0 else exit_code
            # The footer reports what is still dirty, not the pre-commit state.
            pointer_hints = submodules.parent_repos_needing_pointer_commit(submodule_links) or None

    if excluded_count:
        print(f"excluded {excluded_count} repo(s) by exclude patterns", file=sys.stderr)
    report.print_run_footer(
        summary=report.build_run_summary(
            bundles, diagnostics=diagnostics, excluded_count=excluded_count
        ),
        failures=report.collect_run_failures(bundles),
        exit_code=exit_code,
        submodule_pointer_hints=pointer_hints,
        discovery_skips=diagnostics.skipped,
    )
    for bundle in bundles.values():
        for wr in bundle.workspaces:
            wr.owned_edits = {}
    if report_path is not None:
        write_json_atomic(
            report_path,
            report.build_json_payload(
                bundles,
                roots=roots,
                mode="apply" if apply else "plan",
                diagnostics=diagnostics,
                submodules=submodules_json,
                submodule_pointer_hints=pointer_hints,
                exit_code=exit_code,
                excluded_count=excluded_count,
            ),
        )
    return exit_code


def _default_jobs() -> int:
    """Workers for the repo pool, picked at the knee of the throughput curve.

    Total CPU time is near-flat from 2 to 16 workers, so this only decides how
    fast that work is spent. Wall clock falls steeply up to 8 and flattens
    after: on a 32-thread host, 2 workers ran 80% slower than 8 and 4 ran 17%
    slower, while 16 bought 6% for a third more load. The floor matters more
    than the divisor, because gates are mostly serial and too few workers leave
    cores idle.
    """
    return max(4, min(8, (os.cpu_count() or 4) // 4))


def _cap_child_cpu(jobs: int) -> None:
    """Give each worker an equal share of the CPUs via the child environment.

    Go tooling (go build/test, govulncheck, golangci-lint) reads GOMAXPROCS and
    otherwise takes the whole machine per invocation. Uncapped, a sweep burned
    15% more CPU for a 9% wall gain, so the cap buys efficiency rather than
    stability. Setting it in the parent reaches every subprocess by inheritance.
    An explicit GOMAXPROCS from the caller wins.
    """
    share = max(1, (os.cpu_count() or 4) // max(1, jobs))
    os.environ.setdefault("GOMAXPROCS", str(share))


def run_upgrade(*, apply: bool, argv: list[str] | None = None) -> int:
    parser = build_upgrade_parser(apply=apply)
    args = parser.parse_args(argv)

    wire_verbose(args)
    roots = resolve_scan_roots(list(args.roots or DEFAULT_ROOTS), parser)
    if args.jobs is None:
        args.jobs = _default_jobs()
    _cap_child_cpu(args.jobs)
    # The global exclude list joins --exclude here; each repo's own
    # .repo-ops.toml exclude is added per repo in the body.
    args.exclude_patterns = merge_exclude_patterns(
        args.exclude_patterns, load_global_config().get("exclude")
    )

    run_dir = new_run_dir("apply" if apply else "plan")
    log_path, report_path = run_dir / "log", run_dir / "report.json"
    with tee_stdout_stderr(log_path):
        code = _run_upgrade_body(
            apply=apply,
            args=args,
            roots=roots,
            fleet=args.roots == DEFAULT_ROOTS,
            report_path=report_path,
        )
    print(f"repo-ops: log {log_path}  report {report_path}")
    return code


def main_plan(argv: list[str] | None = None) -> int:
    return run_upgrade(apply=False, argv=argv)


def main_apply(argv: list[str] | None = None) -> int:
    return run_upgrade(apply=True, argv=argv)
