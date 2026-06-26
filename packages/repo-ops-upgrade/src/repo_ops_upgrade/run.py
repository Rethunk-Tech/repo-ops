"""Upgrade plan/apply orchestration (shared by console entry points)."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import fnmatch
import os
import sys
import threading
from itertools import groupby
from pathlib import Path
from typing import Literal

from repo_ops_core.cli import (
    build_upgrade_parser,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.config import (
    apply_config_to_env,
    load_global_config,
    load_repo_config,
)
from repo_ops_core.constants import DEFAULT_ROOTS
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

_FAIL_FAST = orchestration.FAIL_FAST_EVENT


def _get_head_sha(git_repo: Path) -> str | None:
    """Return the current HEAD SHA for *git_repo*, or ``None`` on any error."""
    from repo_ops_core.git import run_git

    try:
        result = run_git(git_repo, "rev-parse", "HEAD")
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except Exception:
        return None


def _checkpoint_should_skip(bundle: RepoBundle, checkpoint: dict[str, str]) -> bool:
    """Return ``True`` if *bundle* should be skipped on a ``--resume`` re-run.

    A bundle is skipped when the checkpoint contains its git_repo path mapped
    to the repo's *current* HEAD SHA — i.e. the apply already completed for
    this HEAD and nothing new has been pushed since.

    If the current HEAD differs from the checkpointed SHA (e.g. someone
    force-pushed or new commits landed), the check returns ``False`` and the
    repo is re-processed normally.
    """
    done_sha = checkpoint.get(str(bundle.git_repo))
    if done_sha is None:
        return False
    current_sha = _get_head_sha(bundle.git_repo)
    return current_sha is not None and current_sha == done_sha


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
    args: argparse.Namespace,
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
    # Opt-in per-package targeted fallback (#111); only meaningful with --latest,
    # which is what produces the behind_latest work-list it consumes. All three
    # managers' flags must populate their env var or the CLI flag is a no-op (#131).
    if getattr(args, "yarn_targeted_bump", False):
        os.environ["REPO_OPS_YARN_TARGETED_BUMP"] = "1"
    if getattr(args, "npm_targeted_bump", False):
        os.environ["REPO_OPS_NPM_TARGETED_BUMP"] = "1"
    if getattr(args, "pnpm_targeted_bump", False):
        os.environ["REPO_OPS_PNPM_TARGETED_BUMP"] = "1"
    # --migrate-to-uv-lock: opt in to creating uv.lock for hatch projects (#132).
    if getattr(args, "migrate_to_uv_lock", False):
        os.environ["REPO_OPS_HATCH_MIGRATE_UV_LOCK"] = "1"
    # --yarn-jobs: cap concurrent yarn isolated-copy extractions to avoid /tmp ENOSPC.
    yarn_jobs = getattr(args, "yarn_jobs", None)
    if yarn_jobs is not None:
        os.environ["REPO_OPS_YARN_MAX_CONCURRENCY"] = str(yarn_jobs)
    # --commit-umbrella: opt in to post-commit umbrella pointer bumps (#125).
    if getattr(args, "commit_umbrella", False):
        os.environ["REPO_OPS_COMMIT_UMBRELLA"] = "1"
    # --actions-org-policy: enable org policy enforcement in scan_actions (#125).
    if getattr(args, "actions_org_policy", False):
        os.environ["REPO_OPS_ACTIONS_ORG_POLICY"] = "1"
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
    found = orchestration.filter_workspaces(
        found,
        args.eco_filter,
        getattr(args, "manager_filter", None),
    )

    bundles = workspace_discovery.group_by_repo(found, roots)
    if not args.skip_actions:
        bundles = workspace_discovery.merge_action_only_bundles(
            bundles,
            roots,
            follow_symlinks=follow_symlinks,
        )

    exclude_patterns: list[str] = getattr(args, "exclude_patterns", None) or []
    excluded_count = 0
    if exclude_patterns:
        kept: dict[Path, object] = {}
        for key, bundle in bundles.items():
            label = bundle.label
            path_str = str(key)
            if any(
                pat in label
                or pat in path_str
                or fnmatch.fnmatch(label, pat)
                or fnmatch.fnmatch(path_str, pat)
                for pat in exclude_patterns
            ):
                excluded_count += 1
            else:
                kept[key] = bundle
        bundles = kept  # type: ignore[assignment]

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
                quiet=args.quiet,
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

    # Resume / checkpoint (REPO_OPS_RESUME=1): skip repos that already
    # completed apply in a previous run at their current HEAD SHA.
    _resume_mode = apply and os.environ.get("REPO_OPS_RESUME") == "1"
    _checkpoint: dict[str, str] = {}
    _checkpoint_lock = threading.Lock()
    if _resume_mode:
        from repo_ops_core import state as _cpt_state

        _checkpoint = _cpt_state.load_checkpoint()

    def _process_repo_checkpointed(bundle: RepoBundle) -> list[WorkspaceResult]:
        """Wrap _process_repo with resume-checkpoint skip logic.

        When REPO_OPS_RESUME=1 and the bundle's git_repo HEAD matches the
        checkpointed post-apply SHA, all workspaces are returned as "skip"
        without calling the real process.  After a successful (non-fail) run
        the repo is added to the checkpoint so future resumes can skip it.
        """
        if _resume_mode and _checkpoint_should_skip(bundle, _checkpoint):
            for wr in bundle.workspaces:
                wr.status = "skip"
                wr.error = "resume: already applied"
            return list(bundle.workspaces)

        results = _process_repo(bundle)

        if _resume_mode and not any(r.status == "fail" for r in results):
            post_sha = _get_head_sha(bundle.git_repo)
            if post_sha:
                from repo_ops_core import state as _cpt_state

                with _checkpoint_lock:
                    _checkpoint[str(bundle.git_repo)] = post_sha
                    to_save = dict(_checkpoint)
                _cpt_state.save_checkpoint(to_save)

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
            # Build a depth map: children have depth 0, parents have depth > 0.
            # Bundles at the same depth are independent and can run in parallel.
            pending_keys = [
                key
                for key in bundle_order
                if key in bundles and bundles[key].workspaces
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
            for _level, level_keys_iter in groupby(
                sorted_keys, key=lambda k: depth_map.get(k, 0)
            ):
                if _FAIL_FAST.is_set():
                    break
                level_keys = list(level_keys_iter)
                level_bundles = [bundles[k] for k in level_keys]
                if len(level_bundles) == 1:
                    batch = _process_repo_checkpointed(level_bundles[0])
                    all_results.extend(batch)
                    if _repo_failed(batch):
                        break
                else:
                    level_failed = False
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=args.jobs
                    ) as _level_pool:
                        for batch in run_bounded_pool(
                            _level_pool,
                            level_bundles,
                            _process_repo_checkpointed,
                            max_in_flight=args.jobs,
                            should_stop=_FAIL_FAST.is_set,
                            stop_on_result=_repo_failed,
                        ):
                            all_results.extend(batch)
                            if _FAIL_FAST.is_set():
                                level_failed = True
                                break
                    if level_failed:
                        break
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for batch in run_bounded_pool(
                    pool,
                    pending_bundles,
                    _process_repo_checkpointed,
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
        # Digest refresh (#109): only meaningful while staying SHA-pinned.
        refresh_sha = keep_sha and bool(getattr(args, "actions_refresh_sha", False))
        _raw_tag_style = getattr(args, "actions_tag_style", "major")
        tag_style: actions.TagStyle = "exact" if _raw_tag_style == "exact" else "major"
        # Cross-major apply (#106): explicit --actions-upgrade-mode wins; otherwise
        # --latest is the umbrella that also opts Actions into latest mode.
        _raw_actions_mode = getattr(args, "actions_upgrade_mode", None)
        if _raw_actions_mode is None:
            _raw_actions_mode = (
                "latest" if getattr(args, "latest", False) else "normalize"
            )
        actions_mode: actions.ActionsUpgradeMode = (
            "latest" if _raw_actions_mode == "latest" else "normalize"
        )
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
            try:
                bumps, count = actions.scan_actions(
                    bundle.git_repo,
                    apply=apply,
                    keep_sha=keep_sha,
                    refresh_sha=refresh_sha,
                    tag_style=tag_style,
                    upgrade_mode=actions_mode,
                    tag_cache=local_cache,
                    lint_level=getattr(args, "actions_lint_level", None),
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

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.jobs
        ) as _actions_pool:
            list(_actions_pool.map(_scan_one_bundle_actions, list(bundles.values())))

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

    exit_code = _resolve_exit_code(
        bundles,
        apply=apply,
        strict_exit=args.strict_exit,
        has_actionable_work=_has_actionable_work(bundles),
    )

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
                exit_code=exit_code,
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
                exit_code=exit_code,
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

    if apply:
        commit_mode: Literal["none", "conventional", "agent"] = getattr(
            args, "commit", "none"
        )
        if commit_mode and commit_mode != "none":
            child_outcomes, commit_exit = post_apply_commit.run_post_apply_commits(
                bundles,
                mode=commit_mode,
                dry_run=getattr(args, "commit_dry_run", False),
                allow_dirty=args.allow_dirty,
                commit_on_fail=getattr(args, "commit_on_fail", False),
                quiet=args.quiet,
                max_workers=args.jobs,
            )
            if commit_exit != 0:
                exit_code = 1 if exit_code == 0 else exit_code
            # Umbrella pointer-bump commits: after all child commits complete,
            # commit dirty gitlinks in umbrella repos for children that received
            # commits this run.  Opt-in via REPO_OPS_COMMIT_UMBRELLA=1 env var.
            # CLI flag --commit-umbrella wires the env var (deferred to cli.py).
            if submodule_links:
                _, umbrella_exit = post_apply_commit.commit_umbrella_submodule_pointers(
                    submodule_links,
                    child_outcomes,
                    mode=commit_mode,
                    dry_run=getattr(args, "commit_dry_run", False),
                    quiet=args.quiet,
                )
                if umbrella_exit != 0:
                    exit_code = 1 if exit_code == 0 else exit_code

            # PR/branch mode (opt-in via --pr): after commits land, create a
            # branch, push it, and open a PR for each repo that received a
            # commit this run. Skipped when --commit-dry-run is active (no
            # real commits to push). When --pr is absent, this block is never
            # entered — byte-identical to today's in-place-commit behaviour.
            if getattr(args, "pr", False):
                # Lazy import: pr.py pulls in subprocess + gh; avoid the cost
                # on every apply run when --pr is not set.
                from repo_ops_upgrade import pr as _pr_mod

                commit_dry_run = getattr(args, "commit_dry_run", False)
                if commit_dry_run:
                    if not args.quiet:
                        print(
                            "[pr] dry-run: branch/PR creation skipped",
                            file=sys.stderr,
                        )
                else:
                    committed_repos = {
                        o.git_repo for o in child_outcomes if o.status == "committed"
                    }
                    pr_prefix = getattr(
                        args, "branch_prefix", _pr_mod.DEFAULT_BRANCH_PREFIX
                    )
                    pr_base = getattr(args, "pr_base", None)
                    for bundle in sorted(bundles.values(), key=lambda b: b.label):
                        if bundle.git_repo not in committed_repos:
                            continue
                        pr_outcome = _pr_mod.run_pr_flow(
                            bundle.git_repo,
                            bundle,
                            branch_prefix=pr_prefix,
                            base_branch=pr_base,
                        )
                        if not args.quiet:
                            _pr_mod.print_pr_outcome(pr_outcome)
                        if pr_outcome.status == "failed":
                            exit_code = 1 if exit_code == 0 else exit_code

    if not use_json:
        footer_hints = pointer_hints if apply else None
        if include_submodules and pointer_hints and not apply:
            footer_hints = pointer_hints
        if excluded_count:
            print(f"excluded {excluded_count} repo(s) via --exclude", file=sys.stderr)
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

    # Config-file settings layer (CLI flag > env > config file > default, #TASK-1).
    # Load global + CWD per-repo configs and inject into os.environ for env-var-backed
    # settings before the flag→env wiring in _run_upgrade_body.  Skips any var that's
    # already set (user shell or a prior CLI flag).
    _g_cfg = load_global_config()
    _r_cfg = load_repo_config(Path.cwd())
    apply_config_to_env(global_cfg=_g_cfg, repo_cfg=_r_cfg)

    # ``jobs`` has no env-var channel — apply from config directly to args before the
    # cpu_count fallback so the documented config-file default takes effect.
    if args.jobs is None:
        _jobs_val: object = None
        if _r_cfg and "jobs" in _r_cfg:
            _jobs_val = _r_cfg["jobs"]
        elif _g_cfg and "jobs" in _g_cfg:
            _jobs_val = _g_cfg["jobs"]
        if _jobs_val is not None:
            with contextlib.suppress(TypeError, ValueError):
                args.jobs = max(1, int(_jobs_val))  # type: ignore[arg-type]

    if args.jobs is None:
        args.jobs = min(16, os.cpu_count() or 4)

    # ``exclude_patterns`` has no env-var channel (lists don't serialize well
    # to env vars).  When the CLI flag was not given, apply config-file
    # ``exclude`` directly to args so _run_upgrade_body honours it.
    # CLI --exclude takes precedence: only merge when args.exclude_patterns is
    # falsy (None or empty list from argparse default).
    if not getattr(args, "exclude_patterns", None):
        _excl_cfg: object = None
        if _r_cfg and "exclude" in _r_cfg:
            _excl_cfg = _r_cfg["exclude"]
        elif _g_cfg and "exclude" in _g_cfg:
            _excl_cfg = _g_cfg["exclude"]
        if isinstance(_excl_cfg, list):
            args.exclude_patterns = [str(p) for p in _excl_cfg]
        elif isinstance(_excl_cfg, str) and _excl_cfg:
            args.exclude_patterns = [_excl_cfg]

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
