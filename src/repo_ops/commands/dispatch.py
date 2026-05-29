"""Optional dispatcher: repo-ops <subcommand> (delegates to domain mains)."""

from __future__ import annotations

import argparse
import sys

from repo_ops.inventory.scan import main as inventory_main
from repo_ops.pushed.audit import main as pushed_main
from repo_ops.upgrade.run import main_apply, main_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repo-ops",
        description="Repository operations for local monorepo roots.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("inventory", help="Dependency footprint report (repos-inventory)")
    sub.add_parser(
        "plan",
        help="Upgrade plan: deps + Actions + remediations (repos-upgrade-plan)",
    )
    sub.add_parser(
        "apply",
        help="Apply upgrades (repos-upgrade-apply)",
    )
    sub.add_parser("pushed", help="Pushed commits audit (repos-pushed)")

    args, remainder = parser.parse_known_args(argv)
    if args.command == "inventory":
        inventory_main(remainder or None)
        return 0
    if args.command == "plan":
        return main_plan(remainder or None)
    if args.command == "apply":
        return main_apply(remainder or None)
    if args.command == "pushed":
        return pushed_main(remainder or None)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
