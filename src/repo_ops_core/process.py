"""Subprocess helpers shared by repo-ops commands."""

from __future__ import annotations

import logging
import os
import shutil
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


# Config-write targets redirected for repo commands; package caches stay shared.
_ISOLATED_ENV: dict[str, str] = {}


def isolate_user_config(scratch: Path) -> None:
    """Point user-global tool config writes of repo commands at *scratch*.

    A repo's own build or verify command must not rewrite the operator's
    ``go env -w``, ``npm config``, ``git config --global`` or ``cargo install``
    targets. Seeded copies keep the operator's settings in effect. ``git``
    invocations made by repo-ops itself are exempt in :func:`run`, because
    signing and credentials come from the real config.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    env = {"CARGO_INSTALL_ROOT": str(scratch / "cargo")}
    home = Path.home()
    sources = {
        "GIT_CONFIG_GLOBAL": (
            Path(os.environ.get("GIT_CONFIG_GLOBAL") or home / ".gitconfig"),
            "gitconfig",
        ),
        "NPM_CONFIG_USERCONFIG": (
            Path(os.environ.get("NPM_CONFIG_USERCONFIG") or home / ".npmrc"),
            "npmrc",
        ),
    }
    if shutil.which("go"):
        goenv = subprocess.run(
            ["go", "env", "GOENV"], capture_output=True, text=True, timeout=30, check=False
        ).stdout.strip()
        if goenv:
            sources["GOENV"] = (Path(goenv), "go.env")
    for var, (src, name) in sources.items():
        dest = scratch / name
        if src.is_file():
            shutil.copyfile(src, dest)
        else:
            dest.touch()
        env[var] = str(dest)
    _ISOLATED_ENV.clear()
    _ISOLATED_ENV.update(env)


def run(
    cmd: list[str],
    cwd: Path | str,
    timeout: float = 300,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        msg = f"cwd is not a directory: {cwd_path}"
        raise FileNotFoundError(msg)
    if VERBOSE:
        print(f"  $ {' '.join(cmd)}", file=sys.stderr, flush=True)
    merged = os.environ.copy()
    if cmd[:1] != ["git"]:
        merged.update(_ISOLATED_ENV)
    if env:
        merged.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged,
        input=stdin,
    )
    return proc
