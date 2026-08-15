"""CLI helpers shared by repo-ops commands (argparse types, root handling, common flags)."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.process import LOG_LEVEL_NAMES, configure_logging, set_verbose


class _StoreTrueSetEnv(argparse.Action):
    """``store_true`` that also sets ``os.environ[const] = "1`` when the flag is used."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, True)
        if isinstance(self.const, str) and self.const:
            os.environ[self.const] = "1"


_FIND_PREDICATE_TOKENS = frozenset(
    {
        "-name",
        "-type",
        "-prune",
        "-print",
        "-path",
        "-links",
        "-maxdepth",
        "-mindepth",
    }
)

ECO_FILTER_CHOICES = (
    "node",
    "go",
    "rust",
    "py",
    "deno",
    "container",
    "precommit",
    "actions",
)
ECO_FILTER_HELP = (
    "Limit inventory rows or upgrade workspaces to one ecosystem "
    f"({'/'.join(ECO_FILTER_CHOICES)})."
)

# Package-manager filter choices. Mirrors the registry's CHECKERS keys exactly
# (kept as a static tuple to avoid a core -> upgrade circular import; a test in
# the upgrade suite asserts this stays in sync with CHECKERS).
MANAGER_FILTER_CHOICES = (
    "bun",
    "npm",
    "pnpm",
    "yarn",
    "uv",
    "poetry",
    "pip-tools",
    "hatch",
    "pdm",
    "pipenv",
    "deno",
    "go",
    "rust",
    "container",
    "precommit",
)
MANAGER_FILTER_HELP = (
    "Limit upgrade workspaces to one package manager "
    f"({'/'.join(MANAGER_FILTER_CHOICES)})."
)

DEFAULT_ROOTS_HELP = (
    "Root directories to scan. When omitted, uses DEFAULT_ROOTS: "
    + ", ".join(DEFAULT_ROOTS)
    + ". Pass explicit paths to limit scope (e.g. a single checkout with --flat)."
)

PLAN_DESCRIPTION = (
    "Plan dependency updates, GitHub Actions pin drift, and remediations "
    "(read-only; no lockfile or workflow writes)."
)

APPLY_DESCRIPTION = (
    "Apply dependency updates, GitHub Actions pin bumps, and remediations, "
    "then run per-workspace verify gates unless --no-verify."
)

PLAN_EPILOG = """\
exit codes:
  0  no actionable drift
  1  drift, remediations, or Actions pin changes exist (CI-friendly);
     or any workspace skip/fail when --strict-exit

workflow:
  Run plan before apply with the same flags and roots. Plan may run
  corepack prepare (network) for Yarn version activation.

docs:
  Agents: AGENTS.md (quick reference). Humans: HUMANS.md (install).
  Full reference: docs/usage.md in the repo-ops clone.
"""

APPLY_EPILOG = """\
exit codes:
  0  no workspace failures (dirty skips are ok unless --strict-exit)
  1  any workspace fail; or any skip/fail when --strict-exit

workflow:
  Prefer plan first (same flags/roots). Dirty git worktrees are skipped
  unless --allow-dirty. On verify failure, default --on-verify-fail=revert
  rolls back only apply-artifact files (lockfiles and manifests); use keep
  to leave them in place for inspection. Pre-existing dirty edits and
  untracked files outside that set are preserved either way.

docs:
  Agents: AGENTS.md. Humans: HUMANS.md. Bulk playbook: docs/bulk-maintenance.md.
"""

CONSERVATIVE_FLAG_HELP = (
    "Stay in-range: only move dependencies their own manifest constraints "
    "already allow. The default is a full sweep — cross-range upgrades, "
    "per-package targeted bumps for every package manager, GitHub Actions "
    "pin/digest/major bumps, and container image tag and digest bumps. Use "
    "this when a run must not cross a declared range. Per-ecosystem policy "
    "flags (--go-upgrade-mode, --actions-upgrade-mode) still override it."
)


def positive_int(value: str) -> int:
    """Parse a strictly positive integer for argparse ``type=``."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc
    if n <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


def parse_iso8601(value: str) -> datetime:
    """Parse an ISO 8601 string into a timezone-aware UTC datetime.

    Replaces trailing 'Z' with '+00:00' for Python < 3.11 compatibility
    (fromisoformat only accepts 'Z' natively from 3.11+).
    Rejects naive datetimes with an argparse-compatible error.
    (Compat kept because requires-python >= 3.10 per pyproject.toml.)
    """
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid ISO 8601 datetime {value!r}: {exc}"
        ) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            f"Datetime {value!r} has no timezone; use e.g. 2026-05-26T00:00Z or +00:00 suffix"
        )
    return dt.astimezone(timezone.utc)


def looks_like_find_predicate(value: str) -> bool:
    """True when *value* resembles a find(1) predicate rather than a directory path."""
    text = value.strip()
    if not text:
        return False
    if text.startswith("-"):
        return True
    parts = text.split()
    return any(part in _FIND_PREDICATE_TOKENS for part in parts)


def resolve_scan_roots(
    roots: list[str],
    parser: argparse.ArgumentParser,
) -> list[str]:
    """Resolve roots to absolute paths; reject missing dirs and find-like tokens.

    A root the caller named explicitly must exist — a path that isn't there is a
    typo worth failing on. The built-in defaults are a different case: they
    describe a layout this machine may simply not use, so a missing one is
    skipped rather than fatal, and only an empty result is an error.
    """
    using_defaults = roots == DEFAULT_ROOTS
    resolved: list[str] = []
    for raw in roots:
        text = raw.strip()
        if not text:
            parser.error("root path must not be empty")
        if looks_like_find_predicate(text):
            parser.error(
                f"root {text!r} looks like a find(1) predicate; "
                "pass an existing directory path only"
            )
        path = Path(text).expanduser().resolve()
        if not path.is_dir():
            if using_defaults:
                continue
            if not path.exists():
                parser.error(f"root does not exist: {path}")
            parser.error(f"root is not a directory: {path}")
        resolved.append(str(path))
    if not resolved and using_defaults:
        parser.error(
            "none of the default roots exist on this machine ("
            + ", ".join(DEFAULT_ROOTS)
            + "); pass a directory to scan, e.g. `--flat .` for the current checkout"
        )
    return resolved


def add_follow_symlinks_argument(parser: argparse._ActionsContainer) -> None:
    """Add --follow-symlinks for repo discovery via find -L."""
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help=(
            "Follow symlinks when discovering git repos under roots (find -L). "
            "Default: do not follow (safer for large trees)."
        ),
    )


def add_roots_argument(
    parser: argparse._ActionsContainer,
    *,
    default: list[str] | None = None,
    help_text: str | None = None,
) -> None:
    """Add unified 'roots' positional arg using DEFAULT_ROOTS by default."""
    if default is None:
        default = DEFAULT_ROOTS
    parser.add_argument(
        "roots",
        nargs="*",
        default=default,
        metavar="root_dir",
        help=help_text or DEFAULT_ROOTS_HELP,
    )


def add_json_argument(
    parser: argparse._ActionsContainer, dest: str = "json_out"
) -> None:
    """Add --json flag (common across commands), plus --compact for single-line JSON."""
    parser.add_argument(
        "--json",
        dest=dest,
        action="store_true",
        help="Emit JSON instead of human-readable output.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="With --json: emit single-line JSON (indent=None) instead of pretty-printed.",
    )


def add_json_out_argument(parser: argparse._ActionsContainer) -> None:
    """Add --json-out for plan/apply: write JSON artifact to PATH (requires --json)."""
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        dest="json_report_path",
        help="With --json: write the report to PATH (atomic). Stdout prints only the path.",
    )


def add_flat_argument(parser: argparse._ActionsContainer) -> None:
    """Add --flat flag (treat roots as single projects)."""
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Treat each root as a single project (skip org/proj two-level walk).",
    )


def add_eco_filter_argument(parser: argparse._ActionsContainer) -> None:
    """Add --filter for a single ecosystem (node/go/rust/py)."""
    parser.add_argument(
        "--filter",
        choices=list(ECO_FILTER_CHOICES),
        dest="eco_filter",
        metavar="ECO",
        help=ECO_FILTER_HELP,
    )


def add_manager_filter_argument(parser: argparse._ActionsContainer) -> None:
    """Add --manager for a single package manager (bun/npm/.../go/rust)."""
    parser.add_argument(
        "--manager",
        choices=list(MANAGER_FILTER_CHOICES),
        dest="manager_filter",
        metavar="MANAGER",
        help=MANAGER_FILTER_HELP,
    )


def add_exclude_argument(parser: argparse._ActionsContainer) -> None:
    """Add repeatable repository exclusion patterns."""
    parser.add_argument(
        "--exclude",
        action="append",
        dest="exclude_patterns",
        default=None,
        metavar="PATTERN",
        help=(
            "Exclude repos whose label or absolute path contains PATTERN "
            "(substring or glob). Repeatable."
        ),
    )


_LOG_LEVEL_CHOICES = tuple(sorted(LOG_LEVEL_NAMES))


def add_verbose_argument(parser: argparse._ActionsContainer) -> None:
    """Add --verbose / -v and --log-level flags; wire via wire_verbose(args) after parse."""
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose subprocess logging"
    )
    parser.add_argument(
        "--log-level",
        choices=list(_LOG_LEVEL_CHOICES),
        default=None,
        dest="log_level",
        metavar="LEVEL",
        help=(
            "Set the stdlib root logging level "
            f"({'/'.join(_LOG_LEVEL_CHOICES)}). "
            "--verbose/-v implies debug when --log-level is not set."
        ),
    )


def wire_verbose(args: argparse.Namespace) -> None:
    """Wire parsed --verbose / --log-level to process logging helpers.

    Call after parse_args.  Rules:
    - ``--log-level LEVEL`` configures the stdlib root logger (and sets
      VERBOSE when level is debug).
    - ``--verbose / -v`` enables subprocess echo (VERBOSE=True).  When
      ``--log-level`` is absent, also implies ``--log-level debug``.
    - Both flags may be combined: ``-v --log-level warning`` enables
      subprocess echo at WARNING stdlib level.
    """
    log_level: str | None = getattr(args, "log_level", None)
    verbose: bool = getattr(args, "verbose", False)
    if log_level is not None:
        configure_logging(log_level)
        if verbose:
            set_verbose(True)
    elif verbose:
        configure_logging("debug")


def add_upgrade_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    apply: bool,
) -> None:
    """Flags shared by repo-ops plan and repo-ops apply."""
    # ---- Selection --------------------------------------------------------
    sel = parser.add_argument_group("Selection")
    add_roots_argument(sel)
    add_flat_argument(sel)
    add_eco_filter_argument(sel)
    add_manager_filter_argument(sel)
    add_exclude_argument(sel)
    add_follow_symlinks_argument(sel)

    # ---- Upgrade policy ---------------------------------------------------
    pol = parser.add_argument_group("Upgrade policy")
    pol.add_argument(
        "--conservative",
        action="store_true",
        help=CONSERVATIVE_FLAG_HELP,
    )
    pol.add_argument(
        "--go-upgrade-mode",
        choices=("patch", "explicit", "mvs"),
        default=None,
        dest="go_upgrade_mode",
        metavar="MODE",
        help=(
            "Go module upgrade selection: explicit takes the latest tagged "
            "version; patch bumps within the current minor; mvs uses the "
            "module graph's minimum version selection. A default sweep uses "
            "explicit; --conservative falls back to patch. Setting this "
            "overrides both for this run."
        ),
    )
    pol.add_argument(
        "--actions-upgrade-mode",
        choices=("normalize", "latest"),
        default=None,
        dest="actions_upgrade_mode",
        metavar="MODE",
        help=(
            "GitHub Actions cross-major policy: latest applies cross-major "
            "bumps to the newest available major (honoring --actions-tag-style "
            "and the bare-major safety check); normalize collapses to the "
            "current floating major and only reports newer ones. A default "
            "sweep uses latest; --conservative falls back to normalize. "
            "Setting this overrides both for Actions."
        ),
    )
    pol.add_argument(
        "--actions-tag-style",
        choices=("major", "exact"),
        default="major",
        dest="actions_tag_style",
        metavar="STYLE",
        help=(
            "Tag style when unpinning SHA-pinned Actions (requires --actions-unpin-sha): "
            "major (default) → @vN; exact → highest @vN.M.P within that major."
        ),
    )
    pol.add_argument(
        "--actions-unpin-sha",
        action="store_true",
        dest="actions_unpin_sha",
        help=(
            "Opt in to resolving SHA-pinned Actions to version tags on apply. "
            "By default, SHA pins are preserved (report only) to protect supply-chain hardening."
        ),
    )
    pol.add_argument(
        "--migrate-to-uv-lock",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="migrate_to_uv_lock",
        help=(
            "Create a uv.lock for hatch projects that lack one, converting them to "
            "uv-managed lockfile mode (default). --no-migrate-to-uv-lock skips such "
            "projects instead, which leaves them permanently outside the sweep. "
            "Sets REPO_OPS_HATCH_MIGRATE_UV_LOCK."
        ),
    )
    pol.add_argument(
        "--skip-actions",
        action="store_true",
        help="Skip GitHub Actions workflow pin audit (deps only).",
    )

    # ---- Safety -----------------------------------------------------------
    saf = parser.add_argument_group("Safety")
    saf.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "Process repos with uncommitted changes (default: skip dirty worktrees "
            "and report them in the summary)."
        ),
    )
    saf.add_argument(
        "--strict-exit",
        action="store_true",
        help="Exit 1 when any workspace is skip or fail (default: fail only).",
    )
    if apply:
        saf.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip verify gates after apply.",
        )
        saf.add_argument(
            "--skip-remediations",
            default="",
            metavar="IDS",
            help=(
                "Comma-separated remediation ids to skip "
                "(biome_schema, playwright_browsers, codegen, package_manager_pin, "
                "gofmt, go_generate, ruff_fix, cargo_fix, deno_fix)."
            ),
        )
        saf.add_argument(
            "--verify-test",
            action="store_true",
            help="Run go test ./... and cargo test during verify.",
        )
        saf.add_argument(
            "--on-verify-fail",
            choices=("keep", "revert"),
            default="revert",
            dest="on_verify_fail",
            metavar="POLICY",
            help=(
                "When verify fails after apply: revert only apply-artifact files "
                "(lockfiles and manifests) under the workspace (default), or keep the "
                "changes in the worktree to inspect them. Reverting is the default "
                "because the cross-range bulk upgrade a sweep performs has no "
                "per-package rollback of its own, so a failure would otherwise leave "
                "the workspace broken. Pre-existing dirty edits and untracked files "
                "outside the apply-artifact set are preserved either way."
            ),
        )
        # ---- Resume / checkpoint (apply-only) --------------------------------
        saf.add_argument(
            "--resume",
            action="store_true",
            help=(
                "Skip repos already applied+committed in a previous run at their "
                "current HEAD SHA.  Requires --commit (commits must land so that "
                "the post-commit HEAD is checkpointed; apply-only runs without "
                "--commit produce no checkpoint entries).  Also honoured via "
                "REPO_OPS_RESUME=1.  Scope-aware: a resume with a different "
                "--filter or --manager discards the previous run's checkpoint and "
                "re-processes all repos."
            ),
        )

    # ---- Output -----------------------------------------------------------
    out = parser.add_argument_group("Output")
    add_json_argument(out, dest="json")
    add_json_out_argument(out)
    out.add_argument(
        "--log-file",
        metavar="PATH",
        help="Write combined stdout/stderr transcript to PATH (no shell pipe needed).",
    )
    add_verbose_argument(out)
    out.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-workspace progress lines (summary still prints).",
    )
    out.add_argument(
        "--top",
        type=positive_int,
        default=10,
        help="Max changes shown per workspace in human output.",
    )
    out.add_argument(
        "--max-failures",
        type=int,
        default=20,
        metavar="N",
        dest="max_failures",
        help="Max failure lines in the end-of-run summary (default: 20).",
    )
    # --only-outdated applies to both plan and apply: the filter logic in run.py
    # and the human report are mode-agnostic (both read it via getattr), so it is
    # registered unconditionally (#103).
    out.add_argument(
        "--only-outdated",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Omit clean workspaces from human output (default). "
            "--no-only-outdated lists every workspace scanned. JSON output is "
            "unfiltered either way."
        ),
    )

    # ---- Execution --------------------------------------------------------
    exe = parser.add_argument_group("Execution")
    exe.add_argument(
        "--jobs",
        type=positive_int,
        default=None,
        help="Max parallel workers per run (default: min(16, cpu count)).",
    )
    exe.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop scheduling new workspaces after the first failure.",
    )
    exe.add_argument(
        "--actions-lint-level",
        choices=("block", "warn", "off"),
        default=None,
        dest="actions_lint_level",
        metavar="LEVEL",
        help=(
            "How actionlint failures gate pin bumps: block (default, env: "
            "REPO_OPS_ACTIONS_LINT_LEVEL), warn, or off."
        ),
    )
    exe.add_argument(
        "--submodules",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Detect .gitmodules under roots; include submodules[] in JSON and "
            "call out parent repos needing submodule pointer commits (default). "
            "Detection only — no writes. --no-submodules skips the scan."
        ),
    )
    if apply:
        exe.add_argument(
            "--submodule-order",
            choices=("discovery", "children-first"),
            default="children-first",
            dest="submodule_order",
            metavar="ORDER",
            help=(
                "Repo processing order when submodules exist: children-first "
                "(default) applies child git repos before parent umbrellas, so a "
                "parent never bumps its pointer at a child this run has not "
                "updated yet; discovery uses plain scan order."
            ),
        )
        exe.add_argument(
            "--commit",
            choices=("none", "conventional", "agent"),
            default="none",
            metavar="MODE",
            help=(
                "After apply: commit each repo where apply and verify succeeded. "
                "conventional=local git; agent=commit-agent provider drafts the "
                "subject (model from REPO_OPS_COMMIT_AGENT_MODEL / commit_agent_model, "
                "default "
                "composer-2.5; needs CURSOR_API_KEY and optional "
                "repo-ops-upgrade[commit-agent-cursor])."
            ),
        )
        exe.add_argument(
            "--commit-dry-run",
            action="store_true",
            help="With --commit: print intended messages and file lists; no git writes.",
        )
        exe.add_argument(
            "--commit-on-fail",
            action="store_true",
            help=(
                "Also attempt commits for repos with apply/verify failures "
                "(default: skip failed verify)."
            ),
        )
        exe.add_argument(
            "--commit-umbrella",
            action="store_true",
            dest="commit_umbrella",
            help=(
                "After child commits complete, also commit submodule pointer bumps "
                "in umbrella repos for each child that received a commit this run. "
                "Enable with --commit-umbrella or REPO_OPS_COMMIT_UMBRELLA=1. "
                "Umbrella pointer commits also require --commit=conventional or "
                "--commit=agent; --commit=none does not commit. "
                "Submodule discovery requires --submodules or "
                "--submodule-order=children-first. Child-to-parent apply ordering "
                "also requires --submodule-order=children-first; that is separate "
                "from umbrella commits. No-op when no submodules are detected."
            ),
        )

    # ---- Branch / PR workflow (apply-only, opt-in) ------------------------
    if apply:
        pr_grp = parser.add_argument_group("Branch / PR workflow")
        pr_grp.add_argument(
            "--pr",
            action="store_true",
            dest="pr",
            help=(
                "After apply+commit, create a branch and open a PR via gh for each "
                "repo that received a commit. Requires gh CLI installed and "
                "authenticated. Default: off (in-place commit only, no push)."
            ),
        )
        pr_grp.add_argument(
            "--pr-base",
            default=None,
            dest="pr_base",
            metavar="BRANCH",
            help=(
                "Base branch for opened PRs (default: repo's default branch as "
                "reported by gh). Overrides auto-detection when set."
            ),
        )
        pr_grp.add_argument(
            "--branch-prefix",
            default="repo-ops/deps",
            dest="branch_prefix",
            metavar="PREFIX",
            help=(
                "Branch name prefix for PR branches (default: 'repo-ops/deps'). "
                "Resulting name: {prefix}/{repo-slug}."
            ),
        )

    exe.add_argument(
        "--actions-org-policy",
        action="store_true",
        dest="actions_org_policy",
        help=(
            "Fetch the GitHub org's allowed-actions policy and annotate Actions "
            "rows that violate it (status=policy by default). "
            "Sets REPO_OPS_ACTIONS_ORG_POLICY=1. Requires a GitHub token with "
            "org read-permissions. Combine with --actions-policy-level to warn "
            "or block pin rewrites."
        ),
    )
    exe.add_argument(
        "--actions-policy-level",
        choices=("block", "warn", "report"),
        default=None,
        dest="actions_policy_level",
        metavar="LEVEL",
        help=(
            "How org allowed-actions policy violations gate pin bumps when "
            "--actions-org-policy is set: report (default, annotate only), "
            "warn, or block (skip rewrites; status=policy-blocked). "
            "Sets REPO_OPS_ACTIONS_POLICY_LEVEL."
        ),
    )


def build_upgrade_parser(*, apply: bool) -> argparse.ArgumentParser:
    if apply:
        prog = "repo-ops apply"
        description = APPLY_DESCRIPTION
        epilog = APPLY_EPILOG
    else:
        prog = "repo-ops plan"
        description = PLAN_DESCRIPTION
        epilog = PLAN_EPILOG

    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
        allow_abbrev=False,
    )
    add_upgrade_common_arguments(parser, apply=apply)
    return parser
