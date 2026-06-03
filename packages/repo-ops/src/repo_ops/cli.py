"""repo-ops dispatcher: inventory, plan, apply, pushed."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from repo_ops_inventory.scan import main as inventory_main
from repo_ops_pushed.audit import main as pushed_main
from repo_ops_upgrade.run import main_apply, main_plan

from repo_ops._register import register_all
from repo_ops.install_skill import main as install_skill_main

_HANDLERS: dict[str, Callable[[list[str] | None], int | None]] = {
    "inventory": inventory_main,
    "plan": main_plan,
    "apply": main_apply,
    "pushed": pushed_main,
    "install-skill": install_skill_main,
}


def main(argv: list[str] | None = None) -> int:
    register_all()
    parser = argparse.ArgumentParser(
        prog="repo-ops",
        description="Repository operations for local monorepo roots.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in _HANDLERS:
        help_text = {
            "inventory": "Dependency footprint report",
            "plan": "Upgrade plan: deps + Actions + remediations",
            "apply": "Apply upgrades",
            "pushed": "Pushed commits audit",
            "install-skill": "Install Cursor/Claude agent skill for repo-ops",
        }[name]
        sub.add_parser(name, help=help_text)

    args, remainder = parser.parse_known_args(argv)
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
