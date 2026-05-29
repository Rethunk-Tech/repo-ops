"""CLI helpers shared by repo-ops commands (argparse types, root handling, common flags)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from repo_ops.constants import DEFAULT_ROOTS
from repo_ops.process import set_verbose


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


def add_verbose_argument(parser: argparse.ArgumentParser) -> None:
    """Add --verbose / -v flag; caller must wire via wire_verbose(args) after parse."""
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose subprocess logging"
    )


def wire_verbose(args: argparse.Namespace) -> None:
    """Wire parsed --verbose to process.set_verbose (call after parse_args)."""
    if getattr(args, "verbose", False):
        set_verbose(True)
