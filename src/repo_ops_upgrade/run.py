"""Upgrade plan/apply orchestration (shared by console entry points)."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import functools
import os
import shutil
import subprocess
import sys
import threading
from itertools import groupby
from pathlib import Path
from typing import Any, Literal

from repo_ops_core.cli import (
    build_upgrade_parser,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.config import (
    ambient_env,
    apply_config_to_env,
    checkout_config_scope,
    coerce_bool_setting,
    load_global_config,
    load_repo_config,
    resolve_setting,
    set_ambient_env,
)
from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.exclude import filter_repo_paths, merge_exclude_patterns
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

_FAIL_FAST = orchestration.FAIL_FAST_EVENT


def _scope_fingerprint(args: argparse.Namespace) -> str:
    """Derive a short string that identifies the effective run scope.

    Used as a fingerprint stored in the checkpoint file so that a resume
    with a different ``--filter`` / ``--manager`` scope cannot false-skip
    repos that were never processed by the previous (differently-scoped) run.
    An empty string means "no filter applied" (full-fleet run).
    """
    eco = getattr(args, "eco_filter", None) or ""
    manager = getattr(args, "manager_filter", None) or ""
    parts = []
    if eco:
        parts.append(f"eco={eco}")
    if manager:
        parts.append(f"mgr={manager}")
    return ",".join(parts)


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


# Every gate the default full sweep opens. Each one's settings-table default
# is already "1" (see repo_ops_core.config._SETTINGS_MAP), so the sweep needs
# no write here to turn on — only --conservative writes "0" to each, an
# explicit CLI override that outranks the user's own env var or config entry
# by design (an unset default here would leave those free to win instead).
_SWEEP_ENV_VARS = (
    # Cross-range upgrades per ecosystem.
    "REPO_OPS_BUN_UPDATE_LATEST",
    "REPO_OPS_PNPM_UPDATE_LATEST",
    "REPO_OPS_YARN_UPDATE_LATEST",
    "REPO_OPS_DENO_UPDATE_LATEST",
    "REPO_OPS_CONTAINER_UPDATE_LATEST",
    "REPO_OPS_PRECOMMIT_UPDATE_LATEST",
    "REPO_OPS_SUBMODULE_UPDATE_LATEST",
    # Per-package targeted fallback: moves a behind_latest dep instead of only
    # reporting it. Reversible per package.
    "REPO_OPS_YARN_TARGETED_BUMP",
    "REPO_OPS_NPM_TARGETED_BUMP",
    "REPO_OPS_PNPM_TARGETED_BUMP",
    "REPO_OPS_BUN_TARGETED_BUMP",
    "REPO_OPS_DENO_TARGETED_BUMP",
    "REPO_OPS_RUST_TARGETED_BUMP",
    "REPO_OPS_POETRY_TARGETED_BUMP",
    "REPO_OPS_PDM_TARGETED_BUMP",
    "REPO_OPS_PIPENV_TARGETED_BUMP",
    "REPO_OPS_PIP_TOOLS_TARGETED_BUMP",
    # Digest refresh for things already pinned by SHA.
    "REPO_OPS_CONTAINER_REFRESH_SHA",
    "REPO_OPS_PRECOMMIT_REFRESH_SHA",
    "REPO_OPS_SUBMODULE_REFRESH_SHA",
)


@functools.cache
def _github_token_available() -> bool:
    """Whether org-policy lookups can authenticate, probed once per process.

    The policy fetch shells out to ``gh api``, so the question is whether ``gh``
    itself is authenticated rather than whether any particular variable is set;
    ``gh auth token`` answers exactly that and honors the same precedence gh
    uses for the real call. Any failure means no policy fetch, which is a
    skipped annotation rather than a failed run.
    """
    if not shutil.which("gh"):
        return False
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _cli_flag(args: object, name: str) -> bool:
    """Return True only for an explicit True CLI flag.

    ``bool(getattr(...))`` is unsafe with bare ``MagicMock`` doubles: missing
    attributes auto-create truthy mocks. Identity ``is True`` matches argparse
    ``store_true`` and rejects mocks.
    """
    return getattr(args, name, False) is True


def _single_checkout_config_scope(args: argparse.Namespace, roots: list[str]) -> bool:
    """Return whether the invocation targets the current checkout directly."""
    if not _cli_flag(args, "flat") or len(roots) != 1:
        return False
    return Path(roots[0]).resolve() == Path.cwd().resolve()


def _set_cli_env(env_var: str, value: str) -> None:
    """Set a CLI-derived environment value and mark it outside ambient config."""
    os.environ[env_var] = value
    ambient = ambient_env()
    if isinstance(ambient, dict):
        ambient.pop(env_var, None)


def _run_upgrade_body(
    *,
    apply: bool,
    args: argparse.Namespace,
    roots: list[str],
    follow_symlinks: bool = False,
) -> int:
    # Full sweep is the default: every ecosystem crosses ranges and every
    # package manager gets its per-package targeted fallback. --conservative is
    # the single opt-out for a run that must stay inside declared constraints.
    # Targeted bumps are safe to default on because each one snapshots the
    # workspace and restores it unless the install succeeds *and* the resolved
    # version actually advances (see e.g. yarn.targeted_bump_behind).
    conservative = _cli_flag(args, "conservative")
    latest = not conservative
    # --conservative is an explicit CLI instruction, so it is written
    # unconditionally (beats env/config, same as any other CLI override).
    # The non-conservative "on" case is NOT written here: each of these keys'
    # settings-table default is already "1", so
    # leaving the var unset lets the user's own REPO_OPS_*=0 env var or
    # [tool.repo-ops] config entry override the default through the normal
    # CLI > env > config > default layering instead of being silently
    # clobbered by a forced "1" written here.
    if conservative:
        for _var in _SWEEP_ENV_VARS:
            _set_cli_env(_var, "0")
    if latest:
        os.environ.setdefault("REPO_OPS_GO_UPGRADE_MODE", "explicit")
    # --migrate-to-uv-lock: opt in to creating uv.lock for hatch projects.
    if _cli_flag(args, "migrate_to_uv_lock"):
        _set_cli_env("REPO_OPS_HATCH_MIGRATE_UV_LOCK", "1")
    # --commit-umbrella: opt in to post-commit umbrella pointer bumps.
    if _cli_flag(args, "commit_umbrella"):
        _set_cli_env("REPO_OPS_COMMIT_UMBRELLA", "1")
    # Org allowed-actions policy. Enabled whenever a GitHub token is available,
    # because without it a sweep will happily bump a pin on an Action the org
    # forbids — a clean-looking report and a workflow that fails at startup.
    # At the default `report` level this only annotates rows, so the only cost
    # of running it is the fetch; a tokenless host silently skips it rather
    # than failing the run.
    if _cli_flag(args, "actions_org_policy") or _github_token_available():
        _set_cli_env("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    policy_level = getattr(args, "actions_policy_level", None)
    if isinstance(policy_level, str) and policy_level:
        _set_cli_env("REPO_OPS_ACTIONS_POLICY_LEVEL", policy_level)
    go_mode = getattr(args, "go_upgrade_mode", None)
    if isinstance(go_mode, str) and go_mode:
        _set_cli_env("REPO_OPS_GO_UPGRADE_MODE", go_mode)

    skip_remediations = set()
    if apply:
        skip_remediations = {s.strip() for s in args.skip_remediations.split(",") if s.strip()}

    use_json = args.json
    json_report_path = getattr(args, "json_report_path", None)
    # Always collect discovery diagnostics so found-but-unactionable manifests are
    # counted (and surfaced) in human runs too, not only under --json.
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

    _global_cfg: dict[str, Any] = getattr(args, "_repo_ops_global_config", {})
    _ambient_env: dict[str, str] = getattr(args, "_repo_ops_ambient_env", {})
    _repo_cfgs = {bundle.git_repo: load_repo_config(bundle.git_repo) for bundle in bundles.values()}
    exclude_patterns: list[str] = getattr(args, "exclude_patterns", None) or []
    kept_paths: list[Path] = []
    excluded_count = 0
    for path, bundle in bundles.items():
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

    include_submodules = _cli_flag(args, "submodules")
    submodule_order = getattr(args, "submodule_order", "discovery")
    need_submodule_scan = include_submodules or (apply and submodule_order == "children-first")
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
                    verify_test=apply and args.verify_test,
                    skip_remediations=skip_remediations,
                    allow_dirty=args.allow_dirty,
                    on_verify_fail=getattr(args, "on_verify_fail", "keep"),
                    quiet=args.quiet,
                    repo_started_dirty=repo_started_dirty,
                )
            except Exception as exc:
                wr.status = "fail"
                wr.error = str(exc)
                return wr

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
            for wr in bundle.workspaces:
                updated = _process_one(wr, repo_started_dirty=repo_started_dirty)
                results.append(updated)
                if args.fail_fast and updated.status == "fail":
                    _FAIL_FAST.set()
                    break
            return results

    # Resume / checkpoint (REPO_OPS_RESUME=1 or --resume): skip repos that
    # already completed apply+commit in a previous run at their current HEAD.
    # The scope fingerprint ensures a differently-scoped resume (e.g. different
    # --filter) does not false-skip repos outside the prior run's scope.
    _resume_mode = apply and (os.environ.get("REPO_OPS_RESUME") == "1" or _cli_flag(args, "resume"))
    _checkpoint: dict[str, str] = {}
    _checkpoint_lock = threading.Lock()
    _scope = _scope_fingerprint(args)
    if _resume_mode:
        from repo_ops_core import state as _cpt_state

        _checkpoint = _cpt_state.load_checkpoint()
        # Discard stale checkpoint when the run scope changed: a checkpoint
        # from a narrow --eco-filter run must not skip repos outside that
        # scope on a differently-scoped (or unfiltered) resume.
        if _checkpoint.get("_scope") != _scope:
            _checkpoint = {}

    def _process_repo_checkpointed(bundle: RepoBundle) -> list[WorkspaceResult]:
        """Wrap _process_repo with resume-checkpoint skip logic.

        When REPO_OPS_RESUME=1 (or --resume) and the bundle's git_repo HEAD
        matches the checkpointed post-commit SHA, all workspaces are returned
        as "skip" without calling the real process.

        Checkpoint recording is intentionally NOT done here — it happens only
        after the commit phase completes successfully (see the post-commit block
        in _run_upgrade_body).  This ensures a crash before commit leaves no
        checkpoint entry, so the repo is correctly re-applied on the next resume
        rather than being silently skipped with uncommitted changes.
        """
        if _resume_mode and _checkpoint_should_skip(bundle, _checkpoint):
            for wr in bundle.workspaces:
                wr.status = "skip"
                wr.error = "resume: already applied"
            return list(bundle.workspaces)

        return _process_repo(bundle)

    all_results: list[WorkspaceResult] = []
    if apply:
        ordered_bundles = [
            bundles[key] for key in bundle_order if key in bundles and bundles[key].workspaces
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

    # Key by (path, manager): a single directory can host multiple ecosystems
    # (e.g. npm + container). Path-only keys let the last worker overwrite the
    # earlier result, dropping non-container workspaces from --json/--json-out.
    path_to_result = {(r.workspace.path, r.workspace.manager): r for r in all_results}
    for bundle in bundles.values():
        updated: list[WorkspaceResult] = []
        for wr in bundle.workspaces:
            result_key = (wr.workspace.path, wr.workspace.manager)
            if result_key in path_to_result:
                updated.append(path_to_result[result_key])
            elif _FAIL_FAST.is_set():
                wr.status = "skip"
                wr.error = "fail-fast"
                updated.append(wr)
            else:
                # No result and not fail-fast: the worker crashed and dropped
                # this workspace. Mark it failed rather than re-appending the
                # original (default-status) result as a silent clean pass.
                wr.status = "fail"
                wr.error = "worker crashed (no result returned)"
                updated.append(wr)
        bundle.workspaces = updated

    if not args.skip_actions:
        # SHA pins are preserved by default (supply-chain hardening).
        # Only unpin when the user explicitly opts in via --actions-unpin-sha.
        keep_sha = not _cli_flag(args, "actions_unpin_sha")
        # Digest refresh: only meaningful while staying SHA-pinned.
        # No CLI flag of its own — --conservative is passed
        # through as an explicit CLI override (cli_value wins ahead of
        # env/config) rather than a real flag, so REPO_OPS_ACTIONS_REFRESH_SHA
        # / [tool.repo-ops] actions_refresh_sha still take effect whenever the
        # run is not conservative.
        refresh_sha = keep_sha and coerce_bool_setting(
            resolve_setting(
                "actions_refresh_sha",
                cli_value="0" if conservative else None,
                global_cfg=_global_cfg,
                env=_ambient_env,
            )
        )
        # Pin-to-SHA: a floating tag pin is invisible to refresh_sha,
        # so leaving it opt-in meant the common case — an Action that was
        # never SHA-pinned at all — was silently never hardened.
        pin_sha = keep_sha and coerce_bool_setting(
            resolve_setting(
                "actions_pin_sha",
                cli_value="0" if conservative else None,
                global_cfg=_global_cfg,
                env=_ambient_env,
            )
        )
        _raw_tag_style = getattr(args, "actions_tag_style", "major")
        tag_style: actions.TagStyle = "exact" if _raw_tag_style == "exact" else "major"
        # Cross-major apply: explicit --actions-upgrade-mode wins; otherwise
        # A default sweep also opts Actions into latest mode.
        _raw_actions_mode = getattr(args, "actions_upgrade_mode", None)
        if not isinstance(_raw_actions_mode, str):
            _raw_actions_mode = "latest" if latest else "normalize"
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
            with checkout_config_scope(
                global_cfg=_global_cfg,
                repo_cfg=_repo_cfgs.get(bundle.git_repo),
            ):
                try:
                    bumps, count = actions.scan_actions(
                        bundle.git_repo,
                        apply=apply,
                        keep_sha=keep_sha,
                        refresh_sha=refresh_sha,
                        pin_sha=pin_sha,
                        tag_style=tag_style,
                        upgrade_mode=actions_mode,
                        tag_cache=local_cache,
                        lint_level=resolve_setting(
                            "actions_lint_level",
                            cli_value=getattr(args, "actions_lint_level", None),
                            global_cfg=_global_cfg,
                            repo_cfg=_repo_cfgs.get(bundle.git_repo),
                            env=_ambient_env,
                        ),
                        policy_level=resolve_setting(
                            "actions_policy_level",
                            cli_value=getattr(args, "actions_policy_level", None),
                            global_cfg=_global_cfg,
                            repo_cfg=_repo_cfgs.get(bundle.git_repo),
                            env=_ambient_env,
                        ),
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

    # Display-only concern: the human renderer applies its own visibility gate,
    # so filtering here as well would strip clean workspaces out of the JSON
    # report too, which is meant to be a complete record of what was scanned.
    only_outdated = _cli_flag(args, "only_outdated")

    # Submodule tag advances (apply-only, opt-in via --submodules).
    # Runs BEFORE pointer_hints so that staged gitlinks are visible to the
    # hint scan and are committed by the post-apply commit phase.
    submodule_gitlink_map: dict[Path, set[str]] = {}
    if apply and include_submodules and submodule_links:
        _sub_bumps = submodules.apply_submodule_tag_advances(
            submodule_links,
            allow_dirty=args.allow_dirty,
        )
        for _bump in _sub_bumps:
            if _bump.is_actionable:
                submodule_gitlink_map.setdefault(_bump.parent, set()).add(_bump.path)

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
        compact = _cli_flag(args, "compact")
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
                excluded_count=excluded_count,
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
                excluded_count=excluded_count,
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
        commit_mode: Literal["none", "conventional", "agent"] = getattr(args, "commit", "none")
        if commit_mode and commit_mode != "none":
            child_outcomes, commit_exit = post_apply_commit.run_post_apply_commits(
                bundles,
                mode=commit_mode,
                dry_run=_cli_flag(args, "commit_dry_run"),
                allow_dirty=args.allow_dirty,
                commit_on_fail=_cli_flag(args, "commit_on_fail"),
                quiet=args.quiet,
                max_workers=args.jobs,
                submodule_gitlink_map=submodule_gitlink_map if submodule_gitlink_map else None,
            )
            if commit_exit != 0:
                exit_code = 1 if exit_code == 0 else exit_code

            # Checkpoint committed repos NOW (after commit), so that a crash
            # before reaching this block leaves no entry and the repo is
            # correctly re-applied on the next resume run.  Only "committed"
            # outcomes are recorded — skipped/failed repos are never checkpointed
            # .  Lock is held across the disk write
            # to prevent a lost-update race between concurrent checkpoint saves
            # .
            if _resume_mode:
                from repo_ops_core import state as _cpt_state

                for _co in child_outcomes:
                    if _co.status == "committed":
                        _committed_sha = _get_head_sha(_co.git_repo)
                        if _committed_sha:
                            with _checkpoint_lock:
                                _checkpoint[str(_co.git_repo)] = _committed_sha
                                _checkpoint["_scope"] = _scope
                                _to_save = dict(_checkpoint)
                                _cpt_state.save_checkpoint(_to_save)
            # Umbrella pointer-bump commits: after all child commits complete,
            # commit dirty gitlinks in umbrella repos for children that received
            # commits this run.  Opt-in via REPO_OPS_COMMIT_UMBRELLA=1 env var.
            # CLI flag --commit-umbrella wires the env var.
            if submodule_links:
                _, umbrella_exit = post_apply_commit.commit_umbrella_submodule_pointers(
                    submodule_links,
                    child_outcomes,
                    mode=commit_mode,
                    dry_run=_cli_flag(args, "commit_dry_run"),
                    quiet=args.quiet,
                )
                if umbrella_exit != 0:
                    exit_code = 1 if exit_code == 0 else exit_code

            # PR/branch mode (opt-in via --pr): after commits land, create a
            # branch, push it, and open a PR for each repo that received a
            # commit this run. Skipped when --commit-dry-run is active (no
            # real commits to push). When --pr is absent, this block is never
            # entered — byte-identical to today's in-place-commit behaviour.
            if _cli_flag(args, "pr"):
                # Lazy import: pr.py pulls in subprocess + gh; avoid the cost
                # on every apply run when --pr is not set.
                from repo_ops_upgrade import pr as _pr_mod

                commit_dry_run = _cli_flag(args, "commit_dry_run")
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
                    pr_prefix = getattr(args, "branch_prefix", _pr_mod.DEFAULT_BRANCH_PREFIX)
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
            print(
                f"excluded {excluded_count} repo(s) by exclude patterns",
                file=sys.stderr,
            )
        report.print_run_footer(
            summary=report.build_run_summary(
                bundles,
                diagnostics=diagnostics,
                excluded_count=excluded_count,
            ),
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

    follow_symlinks = _cli_flag(args, "follow_symlinks")
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
