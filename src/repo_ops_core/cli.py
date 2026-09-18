"""CLI helpers shared by repo-ops commands (argparse types, root handling, common flags)."""

from __future__ import annotations

import argparse
from pathlib import Path

from repo_ops_core.constants import DEFAULT_ROOTS
from repo_ops_core.process import configure_logging

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
ECO_FILTER_HELP = f"Limit upgrade workspaces to one ecosystem ({'/'.join(ECO_FILTER_CHOICES)})."

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
  1  drift, remediations, or Actions pin changes exist (CI-friendly)

Every run writes $XDG_STATE_HOME/repo-ops/runs/<utc>-plan/{log,report.json};
both paths are the last line printed. Full reference: docs/usage.md.
"""

APPLY_EPILOG = """\
exit codes:
  0  no workspace failures (dirty skips are ok)
  1  any workspace fail

roots:
  omitted  fleet sweep of DEFAULT_ROOTS: dirty repos are skipped; a repo that
           fails verify is reverted and retried once on its own
  named    that checkout, even if dirty (only files apply touched are
           committed); a failed verify keeps the changes for inspection

Each verified repo gets one conventional commit; nothing is pushed.
Every run writes $XDG_STATE_HOME/repo-ops/runs/<utc>-apply/{log,report.json};
both paths are the last line printed. Full reference: docs/usage.md.
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


def add_eco_filter_argument(parser: argparse._ActionsContainer) -> None:
    """Add --filter for a single ecosystem."""
    parser.add_argument(
        "--filter",
        choices=list(ECO_FILTER_CHOICES),
        dest="eco_filter",
        metavar="ECO",
        help=ECO_FILTER_HELP,
    )


def add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    """Flags every root-scanning subcommand shares: roots, --exclude, --jobs, -v."""
    add_roots_argument(parser)
    parser.add_argument(
        "--exclude",
        action="append",
        dest="exclude_patterns",
        default=None,
        metavar="PAT",
        help=(
            "Skip discovered repos whose label or path contains PAT (substring or "
            "glob). Repeatable; unioned with the config `exclude` list."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=None,
        metavar="N",
        help="Parallel workers (default: min(16, cpu count)).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Echo subprocesses.")


def wire_verbose(args: argparse.Namespace) -> None:
    """Turn --verbose into subprocess echo and debug logging."""
    if getattr(args, "verbose", False):
        configure_logging("debug")


def build_upgrade_parser(*, apply: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops apply" if apply else "repo-ops plan",
        description=APPLY_DESCRIPTION if apply else PLAN_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=APPLY_EPILOG if apply else PLAN_EPILOG,
        allow_abbrev=False,
    )
    add_scan_arguments(parser)
    add_eco_filter_argument(parser)
    if apply:
        parser.add_argument(
            "--no-commit",
            action="store_true",
            help=(
                "Leave changes uncommitted (default: one conventional commit per "
                "verified repo; never pushes)."
            ),
        )
        parser.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip verify gates after apply (implies --no-commit).",
        )
    return parser
