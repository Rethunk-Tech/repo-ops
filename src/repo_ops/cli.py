"""repo-ops dispatcher: inventory, plan, apply, audit, dependabot, sbom, license."""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
from collections.abc import Callable
from dataclasses import dataclass

from repo_ops._register import register_all
from repo_ops.install_skill import (
    build_parser as build_install_skill_parser,
)
from repo_ops.install_skill import (
    main as install_skill_main,
)
from repo_ops_audit.audit import build_audit_parser
from repo_ops_audit.audit import main as audit_main
from repo_ops_core.cli import build_upgrade_parser
from repo_ops_dependabot.dependabot import build_dependabot_parser
from repo_ops_dependabot.dependabot import main as dependabot_main
from repo_ops_doctor.doctor import build_doctor_parser
from repo_ops_doctor.doctor import main as doctor_main
from repo_ops_inventory.scan import build_inventory_parser
from repo_ops_inventory.scan import main as inventory_main
from repo_ops_lockfiles.license_policy import build_license_parser
from repo_ops_lockfiles.license_policy import main as license_main
from repo_ops_lockfiles.sbom import build_sbom_parser
from repo_ops_lockfiles.sbom import main as sbom_main
from repo_ops_upgrade.run import main_apply, main_plan


@dataclass(frozen=True)
class _Subcommand:
    """One dispatcher row: handler + parser-builder + help text, co-located
    so adding a subcommand can't add a handler without its help builder."""

    handler: Callable[[list[str] | None], int | None]
    help_builder: Callable[[], argparse.ArgumentParser]
    help_text: str


_SUBCOMMANDS: dict[str, _Subcommand] = {
    "inventory": _Subcommand(
        inventory_main,
        build_inventory_parser,
        "Dependency footprint report",
    ),
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
    "install-skill": _Subcommand(
        install_skill_main,
        build_install_skill_parser,
        "Install Cursor/Claude agent skill for repo-ops",
    ),
    "audit": _Subcommand(
        audit_main,
        build_audit_parser,
        "Fleet vulnerability sweep (read-only unless --fix)",
    ),
    "dependabot": _Subcommand(
        dependabot_main,
        build_dependabot_parser,
        "List/close superseded Dependabot PRs (or --scaffold dependabot.yml)",
    ),
    "sbom": _Subcommand(
        sbom_main,
        build_sbom_parser,
        "Emit CycloneDX/SPDX SBOM from lockfile dependencies (read-only)",
    ),
    "license": _Subcommand(
        license_main,
        build_license_parser,
        "Scan dependency licenses and flag policy violations (read-only)",
    ),
    "doctor": _Subcommand(
        doctor_main,
        build_doctor_parser,
        "Toolchain/auth preflight: probe known binaries and gh auth (read-only)",
    ),
}

_HANDLERS: dict[str, Callable[[list[str] | None], int | None]] = {
    name: sc.handler for name, sc in _SUBCOMMANDS.items()
}

_HELP_BUILDERS: dict[str, Callable[[], argparse.ArgumentParser]] = {
    name: sc.help_builder for name, sc in _SUBCOMMANDS.items()
}

_REGISTER_COMMANDS = frozenset({"inventory", "plan", "apply", "audit"})

_SUBCOMMAND_HELP: dict[str, str] = {name: sc.help_text for name, sc in _SUBCOMMANDS.items()}


def _resolve_version() -> str:
    """Resolve the installed repo-ops version (the CLI dist carries it)."""
    try:
        return importlib.metadata.version("repo-ops")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


_TOP_LEVEL_DESCRIPTION = """\
Repository operations for local monorepo roots (inventory, plan, apply,
audit, dependabot, sbom, license, doctor).

When no root_dir arguments are passed, subcommands scan DEFAULT_ROOTS
(see repo-ops plan --help). Agents: AGENTS.md quick reference. Humans: HUMANS.md.
"""

_TOP_LEVEL_EPILOG = """\
examples:
  repo-ops plan
  repo-ops apply --log-file apply.log
  repo-ops apply --conservative
  repo-ops inventory --flat /path/to/checkout
  repo-ops audit --fail-on critical
  repo-ops dependabot --close
  repo-ops dependabot --scaffold --write
  repo-ops sbom --format cyclonedx --output sbom.json /path/to/checkout
  repo-ops license --allow MIT,Apache-2.0,ISC /path/to/checkout
  repo-ops doctor --strict

Use repo-ops <subcommand> --help for all flags on that subcommand.
"""


def _wants_subcommand_help(remainder: list[str]) -> bool:
    return remainder in (["--help"], ["-h"])


def main(argv: list[str] | None = None) -> int:
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
