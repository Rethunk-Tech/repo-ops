"""Subprocess helpers shared by repo-ops commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VERBOSE = False


def set_verbose(enabled: bool) -> None:
    global VERBOSE
    VERBOSE = enabled


def run(
    cmd: list[str],
    cwd: Path | str,
    timeout: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if VERBOSE:
        print(f"  $ {' '.join(cmd)}", file=sys.stderr, flush=True)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged,
    )
    if VERBOSE and proc.returncode != 0 and (proc.stderr or proc.stdout):
        err = (proc.stderr or proc.stdout).strip()
        if err:
            print(f"  ! exit {proc.returncode}:\n{err}", file=sys.stderr, flush=True)
    return proc
