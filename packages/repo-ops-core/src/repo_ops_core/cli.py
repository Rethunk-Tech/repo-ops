"""CLI helpers shared by repo-ops commands (argparse types, root handling, common flags)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.process import set_verbose

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

ECO_FILTER_CHOICES = ("node", "go", "rust", "py", "container")
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
    "go",
    "rust",
    "container",
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
  unless --allow-dirty. On verify failure, default --on-verify-fail=keep
  leaves changes; use revert to roll back lockfiles and tracked paths.

docs:
  Agents: AGENTS.md. Humans: HUMANS.md. Bulk playbook: docs/bulk-maintenance.md.
"""

LATEST_FLAG_HELP = (
    "Aggressive / cross-range upgrade mode for this run. Sets "
    "REPO_OPS_BUN_UPDATE_LATEST, REPO_OPS_PNPM_UPDATE_LATEST, and "
    "REPO_OPS_YARN_UPDATE_LATEST; defaults REPO_OPS_GO_UPGRADE_MODE to "
    "explicit when unset; and opts GitHub Actions into "
    "--actions-upgrade-mode=latest. Per ecosystem: bun/pnpm use their "
    "CLI --latest; yarn uses yarn up -R '*' (peer-constrained deps may "
    "stay behind_latest); go uses explicit latest tags; npm has no latest "
    "mode (semver ranges only). An explicit --actions-upgrade-mode or "
    "--go-upgrade-mode overrides the umbrella for that ecosystem."
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
    """Resolve roots to absolute paths; reject missing dirs and find-like tokens."""
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
        if not path.exists():
            parser.error(f"root does not exist: {path}")
        if not path.is_dir():
            parser.error(f"root is not a directory: {path}")
        resolved.append(str(path))
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


def add_verbose_argument(parser: argparse._ActionsContainer) -> None:
    """Add --verbose / -v flag; caller must wire via wire_verbose(args) after parse."""
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose subprocess logging"
    )


def wire_verbose(args: argparse.Namespace) -> None:
    """Wire parsed --verbose to process.set_verbose (call after parse_args)."""
    if getattr(args, "verbose", False):
        set_verbose(True)


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
    sel.add_argument(
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
    add_follow_symlinks_argument(sel)

    # ---- Upgrade policy ---------------------------------------------------
    pol = parser.add_argument_group("Upgrade policy")
    pol.add_argument(
        "--latest",
        action="store_true",
        help=LATEST_FLAG_HELP,
    )
    pol.add_argument(
        "--yarn-targeted-bump",
        action="store_true",
        dest="yarn_targeted_bump",
        help=(
            "Yarn only, requires --latest: for each direct dep that yarn up -R '*' "
            "left behind_latest (peer/group-constrained), try a reversible targeted "
            "yarn up <pkg>@latest and keep it only if the workspace still installs and "
            "the version actually advances. Moves the gap instead of just reporting it; "
            "bumps that fight the resolver are reverted per-package, never committed. "
            "Sets REPO_OPS_YARN_TARGETED_BUMP."
        ),
    )
    pol.add_argument(
        "--npm-targeted-bump",
        action="store_true",
        dest="npm_targeted_bump",
        help=(
            "npm only, requires --latest: for each direct dep that npm update left "
            "behind latest, attempt a targeted npm install <pkg>@latest and keep it "
            "only if the workspace still installs and the version advances. "
            "Sets REPO_OPS_NPM_TARGETED_BUMP."
        ),
    )
    pol.add_argument(
        "--pnpm-targeted-bump",
        action="store_true",
        dest="pnpm_targeted_bump",
        help=(
            "pnpm only, requires --latest: for each direct dep that pnpm update "
            "--latest left behind, attempt a targeted pnpm add <pkg>@latest and keep "
            "it only if the workspace still installs and the version advances. "
            "Sets REPO_OPS_PNPM_TARGETED_BUMP."
        ),
    )
    pol.add_argument(
        "--go-upgrade-mode",
        choices=("patch", "explicit", "mvs"),
        default=None,
        dest="go_upgrade_mode",
        metavar="MODE",
        help=(
            "Go module upgrade selection: patch (default) bumps within the "
            "current minor; explicit takes the latest tagged version; mvs uses "
            "the module graph's minimum version selection. Overrides "
            "REPO_OPS_GO_UPGRADE_MODE for this run."
        ),
    )
    pol.add_argument(
        "--actions-upgrade-mode",
        choices=("normalize", "latest"),
        default=None,
        dest="actions_upgrade_mode",
        metavar="MODE",
        help=(
            "GitHub Actions cross-major policy: normalize (default) collapses to "
            "the current floating major and reports newer majors; latest also "
            "applies cross-major bumps to the newest available major (honoring "
            "--actions-tag-style and the bare-major safety check). Overrides the "
            "--latest umbrella for Actions."
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
    # Backwards-compat alias: --actions-keep-sha was the old flag (preservation
    # is now the default, so this flag becomes a no-op kept to avoid breaking
    # existing scripts that pass it).
    pol.add_argument(
        "--actions-keep-sha",
        action="store_true",
        dest="actions_keep_sha",
        help="Deprecated no-op: SHA pins are preserved by default now.",
    )
    pol.add_argument(
        "--actions-refresh-sha",
        action="store_true",
        dest="actions_refresh_sha",
        help=(
            "Keep SHA-pinned Actions pinned by digest, but advance the digest to "
            "the newest vN.M.P release within the pin's current major (preserves "
            "the # vN comment). Dependabot-compatible digest bumps; supersedes "
            "their SHA-pin PRs. No effect with --actions-unpin-sha."
        ),
    )
    pol.add_argument(
        "--migrate-to-uv-lock",
        action="store_true",
        dest="migrate_to_uv_lock",
        help=(
            "Opt into creating a uv.lock for hatch projects that currently lack one, "
            "converting them to uv-managed lockfile mode. Without this flag, hatch "
            "projects without a uv.lock are skipped on apply. "
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
            help="Comma-separated remediation ids to skip (biome_schema, ...).",
        )
        saf.add_argument(
            "--verify-test",
            action="store_true",
            help="Run go test ./... during verify.",
        )
        saf.add_argument(
            "--on-verify-fail",
            choices=("keep", "revert"),
            default="keep",
            dest="on_verify_fail",
            metavar="POLICY",
            help=(
                "When verify fails after apply: keep changes in the worktree (default) "
                "or revert lockfiles and git-tracked paths under the workspace."
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
        action="store_true",
        help="Omit clean workspaces from human output.",
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
        action="store_true",
        help=(
            "Detect .gitmodules under roots; include submodules[] in JSON and "
            "call out parent repos needing submodule pointer commits."
        ),
    )
    if apply:
        exe.add_argument(
            "--submodule-order",
            choices=("discovery", "children-first"),
            default="discovery",
            dest="submodule_order",
            metavar="ORDER",
            help=(
                "Repo processing order when submodules exist: discovery (default) "
                "or children-first (child git repos before parent umbrellas)."
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
                "subject (default cursor; needs CURSOR_API_KEY and optional "
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
                "Sets REPO_OPS_COMMIT_UMBRELLA=1. No-op when --submodule-order is "
                "not set or no submodules are detected."
            ),
        )
    exe.add_argument(
        "--actions-org-policy",
        action="store_true",
        dest="actions_org_policy",
        help=(
            "Fetch the GitHub org's allowed-actions policy and annotate Actions "
            "rows that violate it (status=policy). "
            "Sets REPO_OPS_ACTIONS_ORG_POLICY=1. Requires a GitHub token with "
            "org read-permissions."
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
    )
    add_upgrade_common_arguments(parser, apply=apply)
    return parser
