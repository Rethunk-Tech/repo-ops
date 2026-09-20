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
PLAN_EPILOG = """\
exit codes: 0 nothing to do, 1 drift exists.
Writes $XDG_STATE_HOME/repo-ops/runs/<utc>-plan/{log,report.json}; both paths
are the last line printed. Reference: docs/usage.md.
"""

APPLY_EPILOG = """\
roots:
  omitted  sweep DEFAULT_ROOTS: dirty repos skipped, failed verify reverted
  named    that checkout, even if dirty (only files apply touched are
           committed); failed verify keeps the changes

exit codes: 0 no workspace failed, 1 any workspace failed.
Writes $XDG_STATE_HOME/repo-ops/runs/<utc>-apply/{log,report.json}; both paths
are the last line printed. Reference: docs/usage.md.
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


def add_scan_arguments(parser: argparse.ArgumentParser) -> None:
    """Flags every root-scanning subcommand shares: roots, --exclude, --jobs, -v."""
    parser.add_argument(
        "roots",
        nargs="*",
        default=DEFAULT_ROOTS,
        metavar="root",
        help="Checkouts to process (default: " + ", ".join(DEFAULT_ROOTS) + ").",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        dest="exclude_patterns",
        default=None,
        metavar="PAT",
        help="Skip discovered repos whose path matches PAT (substring or glob; repeatable).",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=None,
        metavar="N",
        help="Parallel workers (default: cpus/4, capped to 8; each runs a full gate).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Echo subprocesses.")


def wire_verbose(args: argparse.Namespace) -> None:
    """Turn --verbose into subprocess echo and debug logging."""
    if getattr(args, "verbose", False):
        configure_logging("debug")


def build_upgrade_parser(*, apply: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops apply" if apply else "repo-ops plan",
        description=(
            "Update, verify and commit dependencies in each checkout."
            if apply
            else "Preview what apply would change (read-only)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=APPLY_EPILOG if apply else PLAN_EPILOG,
        allow_abbrev=False,
    )
    add_scan_arguments(parser)
    parser.add_argument(
        "--filter",
        choices=list(ECO_FILTER_CHOICES),
        dest="eco_filter",
        metavar="ECO",
        help="One ecosystem: " + "|".join(ECO_FILTER_CHOICES) + ".",
    )
    if apply:
        parser.add_argument(
            "--no-commit",
            action="store_true",
            help="Leave changes uncommitted (default: one commit per verified repo; never pushes).",
        )
        parser.add_argument(
            "--no-verify",
            action="store_true",
            help="Skip the repo gate (implies --no-commit).",
        )
    return parser
