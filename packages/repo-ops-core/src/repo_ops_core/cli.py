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

ECO_FILTER_CHOICES = ("node", "go", "rust", "py")
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
)
MANAGER_FILTER_HELP = (
    "Limit upgrade workspaces to one package manager "
    f"({'/'.join(MANAGER_FILTER_CHOICES)})."
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


def add_follow_symlinks_argument(parser: argparse.ArgumentParser) -> None:
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
    parser: argparse.ArgumentParser,
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
        help=help_text or "Root directories to scan (default: standard local roots)",
    )


def add_json_argument(parser: argparse.ArgumentParser, dest: str = "json_out") -> None:
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


def add_json_out_argument(parser: argparse.ArgumentParser) -> None:
    """Add --json-out for plan/apply: write JSON artifact to PATH (requires --json)."""
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        dest="json_report_path",
        help="With --json: write the report to PATH (atomic). Stdout prints only the path.",
    )


def add_flat_argument(parser: argparse.ArgumentParser) -> None:
    """Add --flat flag (treat roots as single projects)."""
    parser.add_argument(
        "--flat",
        action="store_true",
        help="Treat each root as a single project (skip org/proj two-level walk).",
    )


def add_eco_filter_argument(parser: argparse.ArgumentParser) -> None:
    """Add --filter for a single ecosystem (node/go/rust/py)."""
    parser.add_argument(
        "--filter",
        choices=list(ECO_FILTER_CHOICES),
        dest="eco_filter",
        metavar="ECO",
        help=ECO_FILTER_HELP,
    )


def add_manager_filter_argument(parser: argparse.ArgumentParser) -> None:
    """Add --manager for a single package manager (bun/npm/.../go/rust)."""
    parser.add_argument(
        "--manager",
        choices=list(MANAGER_FILTER_CHOICES),
        dest="manager_filter",
        metavar="MANAGER",
        help=MANAGER_FILTER_HELP,
    )


def add_verbose_argument(parser: argparse.ArgumentParser) -> None:
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
    add_roots_argument(parser)
    add_json_argument(parser, dest="json")
    add_json_out_argument(parser)
    add_flat_argument(parser)
    add_verbose_argument(parser)
    add_eco_filter_argument(parser)
    add_manager_filter_argument(parser)
    add_follow_symlinks_argument(parser)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--jobs", type=positive_int, default=8)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--latest",
        action="store_true",
        help=(
            "Include cross-range (behind-latest) upgrades for bun/pnpm/yarn, and "
            "apply cross-major GitHub Actions bumps (equivalent to "
            "--actions-upgrade-mode=latest). Sets the per-ecosystem latest-mode env "
            "vars before the run. npm has no latest mode; go is controlled by "
            "--go-upgrade-mode; an explicit --actions-upgrade-mode overrides this."
        ),
    )
    parser.add_argument(
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
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--skip-actions",
        action="store_true",
        help="Skip GitHub Actions workflow pin audit (deps only).",
    )
    parser.add_argument(
        "--actions-unpin-sha",
        action="store_true",
        dest="actions_unpin_sha",
        help=(
            "Opt in to resolving SHA-pinned Actions to version tags on apply. "
            "By default, SHA pins are preserved (report only) to protect supply-chain hardening."
        ),
    )
    # Backwards-compat alias: --actions-keep-sha was the old flag (it was the default
    # behaviour is now the default, so this flag becomes a no-op, but kept to avoid
    # breaking existing scripts that pass it).
    parser.add_argument(
        "--actions-keep-sha",
        action="store_true",
        dest="actions_keep_sha",
        help="Deprecated no-op: SHA pins are preserved by default now.",
    )
    parser.add_argument(
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
    parser.add_argument(
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
    parser.add_argument(
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
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help="Write combined stdout/stderr transcript to PATH (no shell pipe needed).",
    )
    parser.add_argument(
        "--strict-exit",
        action="store_true",
        help="Exit 1 when any workspace is skip or fail (default: fail only).",
    )
    parser.add_argument(
        "--top",
        type=positive_int,
        default=10,
        help="Max changes shown per workspace in human output.",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=20,
        metavar="N",
        dest="max_failures",
        help="Max failure lines in the end-of-run summary (default: 20).",
    )
    if apply:
        parser.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip verify gates after apply.",
        )
        parser.add_argument(
            "--skip-remediations",
            default="",
            metavar="IDS",
            help="Comma-separated remediation ids to skip (biome_schema, ...).",
        )
        parser.add_argument(
            "--verify-test",
            action="store_true",
            help="Run go test ./... during verify.",
        )
        parser.add_argument(
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
    # --only-outdated applies to both plan and apply: the filter logic in run.py
    # and the human report are mode-agnostic (both read it via getattr), so it is
    # registered unconditionally (#103).
    parser.add_argument(
        "--only-outdated",
        action="store_true",
        help="Omit clean workspaces from human output.",
    )
    parser.add_argument(
        "--submodules",
        action="store_true",
        help=(
            "Detect .gitmodules under roots; include submodules[] in JSON and "
            "call out parent repos needing submodule pointer commits."
        ),
    )
    if apply:
        parser.add_argument(
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
        parser.add_argument(
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
        parser.add_argument(
            "--commit-dry-run",
            action="store_true",
            help="With --commit: print intended messages and file lists; no git writes.",
        )
        parser.add_argument(
            "--commit-on-fail",
            action="store_true",
            help=(
                "Also attempt commits for repos with apply/verify failures "
                "(default: skip failed verify)."
            ),
        )


def build_upgrade_parser(*, apply: bool) -> argparse.ArgumentParser:
    if apply:
        description = (
            "Apply dependency updates, GitHub Actions pin bumps, and remediations."
        )
        prog = "repo-ops apply"
    else:
        description = (
            "Plan dependency updates, GitHub Actions pin drift, and remediations "
            "(read-only; no writes)."
        )
        prog = "repo-ops plan"

    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_upgrade_common_arguments(parser, apply=apply)
    return parser
