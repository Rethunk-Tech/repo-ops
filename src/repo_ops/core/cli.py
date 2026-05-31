"""CLI helpers shared by repo-ops commands (argparse types, root handling, common flags)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from repo_ops.core.constants import DEFAULT_ROOTS
from repo_ops.core.process import set_verbose


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
    """Add --json flag (common across commands)."""
    parser.add_argument(
        "--json",
        dest=dest,
        action="store_true",
        help="Emit JSON instead of human-readable output.",
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
        choices=["node", "go", "rust", "py"],
        dest="eco_filter",
        metavar="ECO",
        help="Only include this ecosystem (node/go/rust/py).",
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
    """Flags shared by repos-upgrade-plan and repos-upgrade-apply."""
    add_roots_argument(parser)
    add_json_argument(parser, dest="json_out")
    add_flat_argument(parser)
    add_verbose_argument(parser)
    add_eco_filter_argument(parser)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--skip-actions",
        action="store_true",
        help="Skip GitHub Actions workflow pin audit (deps only).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max changes shown per workspace in human output.",
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
    else:
        parser.add_argument(
            "--only-outdated",
            action="store_true",
            help="Omit clean workspaces from human output.",
        )


def build_upgrade_parser(*, apply: bool) -> argparse.ArgumentParser:
    if apply:
        description = (
            "Apply dependency updates, GitHub Actions pin bumps, and remediations."
        )
        prog = "repos-upgrade-apply"
    else:
        description = (
            "Plan dependency updates, GitHub Actions pin drift, and remediations "
            "(read-only; no writes)."
        )
        prog = "repos-upgrade-plan"

    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_upgrade_common_arguments(parser, apply=apply)
    return parser


def reject_legacy_upgrade_flags(argv: list[str] | None) -> None:
    """Fail fast on removed dep_refresh flags with migration hints."""
    if argv is None:
        argv = sys.argv[1:]
    legacy = {
        "--also-actions": "Actions are included by default; use --skip-actions to omit.",
        "--only-actions": (
            "Removed: run repos-upgrade-plan/apply (deps + actions). "
            "Use --skip-actions only if you need dependency work without Actions."
        ),
        "-y": "Use repos-upgrade-apply instead of -y on repos-upgrade-plan.",
        "--yes": "Use repos-upgrade-apply instead of --yes on repos-upgrade-plan.",
    }
    for flag, hint in legacy.items():
        if flag in argv:
            sys.exit(f"error: {flag} is no longer supported. {hint}")
