#!/usr/bin/env python3
"""Run vendored doc-audit --strict for CI and local doc governance checks."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent / "doc-audit.py"


def _repo_root(start: Path) -> Path:
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    return start.resolve()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: walk up to .git)",
    )
    args = ap.parse_args(argv)
    root = args.root.resolve() if args.root else _repo_root(Path.cwd())

    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root), "--strict"],
        check=False,
    )
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
