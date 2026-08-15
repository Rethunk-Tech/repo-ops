"""Subprocess helpers shared by repo-ops commands."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

VERBOSE = False

LOG_LEVEL_NAMES = frozenset({"debug", "info", "warning", "error"})


def set_verbose(enabled: bool) -> None:
    global VERBOSE
    VERBOSE = enabled


def configure_logging(level: str | None) -> None:
    """Configure the stdlib root logger for the given *level* string.

    Accepted values: ``'debug'``, ``'info'``, ``'warning'``, ``'error'``
    (case-insensitive).  Passing ``None`` is a no-op so callers can
    unconditionally forward ``args.log_level`` even when the argument
    was not parsed.

    Uses ``logging.basicConfig(force=True)`` (Python ≥ 3.8) so the call
    is idempotent and safe to invoke after any third-party logging setup.
    When *level* is ``'debug'``, also sets the process-level VERBOSE flag
    so subprocess commands are echoed to stderr (same as ``--verbose``).
    """
    if level is None:
        return
    normalized = level.lower()
    if normalized not in LOG_LEVEL_NAMES:
        return
    numeric = getattr(logging, normalized.upper(), None)
    if not isinstance(numeric, int):
        return
    logging.basicConfig(
        level=numeric,
        format="%(levelname)s %(name)s %(message)s",
        force=True,
    )
    if normalized == "debug":
        set_verbose(True)


def run(
    cmd: list[str],
    cwd: Path | str,
    timeout: float = 300,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        msg = f"cwd is not a directory: {cwd_path}"
        raise FileNotFoundError(msg)
    if VERBOSE:
        print(f"  $ {' '.join(cmd)}", file=sys.stderr, flush=True)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd_path),
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
