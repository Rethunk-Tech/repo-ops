"""Argument parsers for repos-upgrade-plan and repos-upgrade-apply."""

from __future__ import annotations

import argparse
import sys

from repo_ops.core.cli import add_upgrade_common_arguments


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


def reject_legacy_flags(argv: list[str] | None) -> None:
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
