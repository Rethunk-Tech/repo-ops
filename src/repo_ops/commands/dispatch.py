"""Optional dispatcher: repo-ops <subcommand> (delegates to domain mains)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from repo_ops.inventory.scan import main as inventory_main
from repo_ops.pushed.audit import main as pushed_main
from repo_ops.upgrade.run import main_apply, main_plan

# Subcommand -> handler(remainder argv). Inventory returns None; others return exit code.
_HANDLERS: dict[str, Callable[[list[str] | None], int | None]] = {
    "inventory": inventory_main,
    "plan": main_plan,
    "apply": main_apply,
    "pushed": pushed_main,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repo-ops",
        description="Repository operations for local monorepo roots.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in _HANDLERS:
        help_text = {
            "inventory": "Dependency footprint report (repos-inventory)",
            "plan": "Upgrade plan: deps + Actions + remediations (repos-upgrade-plan)",
            "apply": "Apply upgrades (repos-upgrade-apply)",
            "pushed": "Pushed commits audit (repos-pushed)",
        }[name]
        sub.add_parser(name, help=help_text)

    args, remainder = parser.parse_known_args(argv)
    handler = _HANDLERS[args.command]
    code = handler(remainder or None)
    return 0 if code is None else code


if __name__ == "__main__":
    sys.exit(main())
