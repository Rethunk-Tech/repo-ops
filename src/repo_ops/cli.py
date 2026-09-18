"""repo-ops dispatcher: plan, apply, dependabot."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

from repo_ops._register import register_all
from repo_ops_core.cli import build_upgrade_parser
from repo_ops_dependabot.dependabot import build_dependabot_parser
from repo_ops_dependabot.dependabot import main as dependabot_main
from repo_ops_upgrade.run import main_apply, main_plan


@dataclass(frozen=True)
class _Subcommand:
    """One dispatcher row: handler + parser-builder + help text, co-located
    so adding a subcommand can't add a handler without its help builder."""

    handler: Callable[[list[str] | None], int | None]
    help_builder: Callable[[], argparse.ArgumentParser]
    help_text: str


_SUBCOMMANDS: dict[str, _Subcommand] = {
    "plan": _Subcommand(
        main_plan,
        lambda: build_upgrade_parser(apply=False),
        "Upgrade plan: deps + Actions + remediations (read-only)",
    ),
    "apply": _Subcommand(
        main_apply,
        lambda: build_upgrade_parser(apply=True),
        "Apply upgrades with verify gates",
    ),
    "dependabot": _Subcommand(
        dependabot_main,
        build_dependabot_parser,
        "List superseded Dependabot PRs; --close closes them",
    ),
}

_HANDLERS: dict[str, Callable[[list[str] | None], int | None]] = {
    name: sc.handler for name, sc in _SUBCOMMANDS.items()
}

_HELP_BUILDERS: dict[str, Callable[[], argparse.ArgumentParser]] = {
    name: sc.help_builder for name, sc in _SUBCOMMANDS.items()
}

_REGISTER_COMMANDS = frozenset({"plan", "apply"})

_SUBCOMMAND_HELP: dict[str, str] = {name: sc.help_text for name, sc in _SUBCOMMANDS.items()}


def _resolve_version() -> str:
    """Resolve the installed repo-ops version (the CLI dist carries it)."""
    try:
        return importlib.metadata.version("repo-ops")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


_TOP_LEVEL_DESCRIPTION = """\
Dependency maintenance for local checkouts (plan, apply, dependabot).

When no root_dir arguments are passed, subcommands scan DEFAULT_ROOTS
(see repo-ops plan --help).
"""

_TOP_LEVEL_EPILOG = """\
examples:
  repo-ops apply
  repo-ops apply /path/to/checkout
  repo-ops plan /path/to/checkout
  repo-ops dependabot --close

Use repo-ops <subcommand> --help for all flags on that subcommand.
"""


def _wants_subcommand_help(remainder: list[str]) -> bool:
    return remainder in (["--help"], ["-h"])


def main(argv: list[str] | None = None) -> int:
    # Parallel workers run `git status` in umbrella repos while sibling workers
    # `git add` in their submodules; status's opportunistic index refresh
    # recurses into submodules and takes their index.lock, failing the add
    # (27/300 adds in a local repro, 0/300 with this set). Inherited by every
    # git subprocess, including ones gates spawn.
    os.environ.setdefault("GIT_OPTIONAL_LOCKS", "0")
    if (
        argv is not None
        and len(argv) >= 2
        and argv[0] in _HELP_BUILDERS
        and _wants_subcommand_help(argv[1:])
    ):
        _HELP_BUILDERS[argv[0]]().print_help()
        return 0

    parser = argparse.ArgumentParser(
        prog="repo-ops",
        description=_TOP_LEVEL_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_TOP_LEVEL_EPILOG,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"repo-ops {_resolve_version()}",
        help="Print the repo-ops version and exit.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in _SUBCOMMAND_HELP.items():
        # add_help=False: delegate --help to the full subcommand parser so flags
        # and root_dir are documented; parse_known_args forwards real argv.
        sub.add_parser(name, help=help_text, add_help=False)

    args, remainder = parser.parse_known_args(argv)
    if _wants_subcommand_help(remainder):
        _HELP_BUILDERS[args.command]().print_help()
        return 0

    if args.command in _REGISTER_COMMANDS:
        register_all()

    handler = _HANDLERS[args.command]
    # Forward the remainder as-is (possibly an empty list). Using `remainder or
    # None` would pass None for a bare subcommand (e.g. `repo-ops plan`), and the
    # handler's argparse then falls back to sys.argv[1:] — re-consuming the
    # subcommand token "plan" as a positional root. Passing [] lets defaults
    # (DEFAULT_ROOTS) apply correctly.
    code = handler(remainder)
    return 0 if code is None else code


if __name__ == "__main__":
    sys.exit(main())
