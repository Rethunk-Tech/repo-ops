"""CLI helpers shared by repo-ops commands (argparse types, root handling, common flags)."""

from __future__ import annotations

import argparse
from pathlib import Path

from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.process import LOG_LEVEL_NAMES, configure_logging, set_verbose

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
    f"Limit inventory rows or upgrade workspaces to one ecosystem ({'/'.join(ECO_FILTER_CHOICES)})."
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
    f"Limit upgrade workspaces to one package manager ({'/'.join(MANAGER_FILTER_CHOICES)})."
)

DEFAULT_ROOTS_HELP = (
    "Root directories to scan. When omitted, uses DEFAULT_ROOTS: "
    + ", ".join(DEFAULT_ROOTS)
    + ". Named roots are treated as checkouts the operator is working in."
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

Full reference: docs/usage.md.
"""

APPLY_EPILOG = """\
exit codes:
  0  no workspace failures (dirty skips are ok unless --strict-exit)
  1  any workspace fail; or any skip/fail when --strict-exit

roots:
  omitted  fleet sweep of DEFAULT_ROOTS: dirty repos are skipped; a repo that
           fails verify is reverted and retried once on its own
  named    that checkout, even if dirty (only files apply touched are
           committed); a failed verify keeps the changes for inspection

Each verified repo gets one conventional commit; nothing is pushed.
Full reference: docs/usage.md.
"""


def positive_int(value: str) -> int:
    """Parse a strictly positive integer for argparse ``type=``."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc
    if n <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


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
            + "); pass a directory to scan, e.g. `.` for the current checkout"
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


def add_json_argument(parser: argparse._ActionsContainer, dest: str = "json_out") -> None:
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
    add_eco_filter_argument(sel)
    add_manager_filter_argument(sel)
    add_exclude_argument(sel)
    add_follow_symlinks_argument(sel)

    # ---- Upgrade policy ---------------------------------------------------
    pol = parser.add_argument_group("Upgrade policy")
    pol.add_argument(
        "--skip-actions",
        action="store_true",
        help="Skip GitHub Actions workflow pin audit (deps only).",
    )

    # ---- Safety -----------------------------------------------------------
    saf = parser.add_argument_group("Safety")
    saf.add_argument(
        "--strict-exit",
        action="store_true",
        help="Exit 1 when any workspace is skip or fail (default: fail only).",
    )
    if apply:
        saf.add_argument(
            "--no-commit",
            action="store_true",
            help=(
                "Leave changes uncommitted (default: one conventional commit per "
                "verified repo; never pushes)."
            ),
        )
        saf.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip verify gates after apply (implies --no-commit).",
        )
        saf.add_argument(
            "--skip-remediations",
            default="",
            metavar="IDS",
            help=(
                "Comma-separated remediation ids to skip "
                "(biome_schema, playwright_browsers, codegen, package_manager_pin, manifest_format, "
                "gofmt, go_generate, ruff_fix, cargo_fix, deno_fix)."
            ),
        )
        saf.add_argument(
            "--verify-test",
            action="store_true",
            help="Run go test ./... and cargo test during verify.",
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
    # registered unconditionally.
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
