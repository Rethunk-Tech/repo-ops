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
from typing import Any

from repo_ops_core.cli import (
    build_upgrade_parser,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.config import (
    apply_config_to_env,
    checkout_config_scope,
    load_global_config,
    load_repo_config,
    set_ambient_env,
)
from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.exclude import filter_repo_paths, merge_exclude_patterns
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
    reinstall_from_lockfile,
    restore_worktree_state,
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
    tree_ok = restore_worktree_state(repo, repo_before)
    for wr in results:
        # A verify-failed workspace already reverted and reinstalled itself.
        if wr.status == "skip" or wr.verify_fail_disposition is not None:
            continue
        installed_ok = reinstall_from_lockfile(wr.workspace)
        if wr.status == "ok":
            wr.status = "skip"
            wr.error = "reverted: another workspace in this repo failed"
        wr.reverted_ok = wr.reverted_ok and tree_ok and installed_ok


def _cli_flag(args: object, name: str) -> bool:
    """Return True only for an explicit True CLI flag.

    ``bool(getattr(...))`` is unsafe with bare ``MagicMock`` doubles: missing
    attributes auto-create truthy mocks. Identity ``is True`` matches argparse
    ``store_true`` and rejects mocks.
    """
    return getattr(args, name, False) is True


def _single_checkout_config_scope(args: argparse.Namespace, roots: list[str]) -> bool:
    """Return whether the invocation targets the current checkout directly."""
    if len(roots) != 1:
        return False
    return Path(roots[0]).resolve() == Path.cwd().resolve()


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
    commit = apply and not _cli_flag(args, "no_commit") and not args.no_verify
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
    bundles = workspace_discovery.merge_action_only_bundles(bundles, roots)

    _global_cfg: dict[str, Any] = getattr(args, "_repo_ops_global_config", {})
    _ambient_env: dict[str, str] = getattr(args, "_repo_ops_ambient_env", {})
    _repo_cfgs = {bundle.git_repo: load_repo_config(bundle.git_repo) for bundle in bundles.values()}
    exclude_patterns: list[str] = getattr(args, "exclude_patterns", None) or []
    kept_paths: list[Path] = []
    excluded_count = 0
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
    bundles = {path: bundles[path] for path in kept_paths}

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
        with checkout_config_scope(
            global_cfg=_global_cfg,
            repo_cfg=_repo_cfgs.get(wr.workspace.git_repo),
        ):
            try:
                return orchestration.process_workspace(
                    wr,
                    do_apply=apply,
                    do_verify=apply and not args.no_verify,
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
        with checkout_config_scope(
            global_cfg=_global_cfg,
            repo_cfg=_repo_cfgs.get(bundle.git_repo),
        ):
            results: list[WorkspaceResult] = []
            # Snapshot the repo's dirty state once, before any workspace in this
            # bundle is processed, rather than re-checking live per workspace. All
            # workspaces in a bundle share bundle.git_repo and share one commit at
            # the end of the run, so an earlier sibling's own already-applied (not
            # yet committed) changes must not make a later sibling look dirty and
            # get skipped outright.
            repo_started_dirty = is_git_dirty(bundle.git_repo) if apply else None
            repo_before = capture_worktree_state(bundle.git_repo) if rollback_on_veto else None
            for wr in bundle.workspaces:
                updated = _process_one(wr, repo_started_dirty=repo_started_dirty)
                results.append(updated)
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
            all_results = [r for r in all_results if r.workspace.git_repo not in retry_set]
            for key in failed_repos:
                bundle = bundles[key]
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
    _tag_cache_lock = threading.Lock()

    def _scan_one_bundle_actions(bundle: RepoBundle) -> None:
        if apply and (bundle.any_fail() or bundle.any_dirty_skip()):
            return
        local_cache: dict[str, dict | None] = {}
        with _tag_cache_lock:
            local_cache.update(run_tag_cache)
        with checkout_config_scope(
            global_cfg=_global_cfg,
            repo_cfg=_repo_cfgs.get(bundle.git_repo),
        ):
            try:
                bumps, count = actions.scan_actions(
                    bundle.git_repo,
                    apply=apply,
                    tag_cache=local_cache,
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as _actions_pool:
        list(_actions_pool.map(_scan_one_bundle_actions, list(bundles.values())))

    # Submodule tag advances (apply-only).
    # Runs BEFORE pointer_hints so that staged gitlinks are visible to the
    # hint scan and are committed by the post-apply commit phase.
    submodule_gitlink_map: dict[Path, set[str]] = {}
    if apply and submodule_links:
        _sub_bumps = submodules.apply_submodule_tag_advances(
            submodule_links,
            allow_dirty=allow_dirty,
        )
        for _bump in _sub_bumps:
            if _bump.is_actionable:
                submodule_gitlink_map.setdefault(_bump.parent, set()).add(_bump.path)

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


def run_upgrade(*, apply: bool, argv: list[str] | None = None) -> int:
    parser = build_upgrade_parser(apply=apply)
    args = parser.parse_args(argv)

    # Config-file settings layer (CLI flag > env > config file > default).
    _g_cfg = load_global_config()

    wire_verbose(args)

    roots = resolve_scan_roots(list(args.roots or DEFAULT_ROOTS), parser)
    _single_checkout = _single_checkout_config_scope(args, roots)
    _r_cfg = load_repo_config(Path.cwd()) if _single_checkout else {}
    # Keep the compatibility environment injection for a direct single-checkout
    # invocation. Fleet runs resolve repo-scoped settings at use instead of
    # leaking the launch checkout's config into every worker.
    apply_config_to_env(
        global_cfg=_g_cfg if _single_checkout else {},
        repo_cfg=_r_cfg,
    )
    # Snapshot after config injection so injected config values remain ambient;
    # later CLI flag writes are the only process values classified as overrides.
    _ambient_env = dict(os.environ)
    set_ambient_env(_ambient_env)
    args._repo_ops_global_config = _g_cfg
    args._repo_ops_ambient_env = _ambient_env

    # ``jobs`` has no env-var channel — apply from config directly to args before the
    # cpu_count fallback so the documented config-file default takes effect.
    if args.jobs is None:
        _jobs_val: Any = None
        if _r_cfg and "jobs" in _r_cfg:
            _jobs_val = _r_cfg["jobs"]
        elif _g_cfg and "jobs" in _g_cfg:
            _jobs_val = _g_cfg["jobs"]
        if _jobs_val is not None:
            with contextlib.suppress(TypeError, ValueError):
                args.jobs = max(1, int(_jobs_val))

    if args.jobs is None:
        args.jobs = min(16, os.cpu_count() or 4)

    # ``exclude_patterns`` has no env-var channel (lists don't serialize well
    # to env vars). Merge CLI and every config layer directly before scanning.
    args.exclude_patterns = merge_exclude_patterns(
        getattr(args, "exclude_patterns", None),
        (_r_cfg or {}).get("exclude"),
        (_g_cfg or {}).get("exclude"),
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
