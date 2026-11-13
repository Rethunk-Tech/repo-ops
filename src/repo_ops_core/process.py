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
    ``go env -w``, ``npm config``, ``pip config``, ``uv`` user config,
    ``git config --global`` or ``cargo install`` targets. Seeded copies keep
    the operator's settings in effect. ``git``
    invocations made by repo-ops itself are exempt in :func:`run`, because
    signing and credentials come from the real config.
    """
    # The seeded copies carry the operator's registry tokens and credential helpers.
    scratch.mkdir(parents=True, exist_ok=True)
    scratch.chmod(0o700)
    env = {"CARGO_INSTALL_ROOT": str(scratch / "cargo")}
    home = Path.home()
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    sources = {
        "GIT_CONFIG_GLOBAL": (
            Path(os.environ.get("GIT_CONFIG_GLOBAL") or home / ".gitconfig"),
            "gitconfig",
        ),
        "NPM_CONFIG_USERCONFIG": (
            Path(os.environ.get("NPM_CONFIG_USERCONFIG") or home / ".npmrc"),
            "npmrc",
        ),
        "PIP_CONFIG_FILE": (
            Path(os.environ.get("PIP_CONFIG_FILE") or xdg / "pip" / "pip.conf"),
            "pip.conf",
        ),
        "UV_CONFIG_FILE": (
            Path(os.environ.get("UV_CONFIG_FILE") or xdg / "uv" / "uv.toml"),
            "uv.toml",
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
        dest.unlink(missing_ok=True)
        dest.touch(mode=0o600)
        if src.is_file():
            shutil.copyfile(src, dest)
        env[var] = str(dest)
    _ISOLATED_ENV.clear()
    _ISOLATED_ENV.update(env)


def _tool_cache_dirs() -> list[str]:
    """Host paths that builds need to write: Go, bun, npm, uv and cargo caches, and gate's own dirs."""
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        path = raw.strip()
        if not path or path == "undefined" or path in seen:
            return
        p = Path(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        resolved = str(p.resolve())
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(resolved)

    def tool_out(cmd: list[str]) -> str:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except OSError:
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout

    if shutil.which("go"):
        for line in tool_out(["go", "env", "GOCACHE", "GOMODCACHE"]).splitlines():
            add(line)
    if shutil.which("npm"):
        add(tool_out(["npm", "config", "get", "cache"]))
    bun_cache = os.environ.get("BUN_INSTALL_CACHE_DIR")
    if bun_cache:
        add(bun_cache)
    elif shutil.which("bun"):
        bun_out = tool_out(["bun", "pm", "cache"]).strip()
        if bun_out:
            add(bun_out)
        else:
            add(str(Path(os.environ.get("HOME") or Path.home()) / ".bun" / "install" / "cache"))
    if shutil.which("gate"):
        # gate's child TMPDIR and logs default to /var/tmp; results and locks live in its XDG dirs.
        home = Path(os.environ.get("HOME") or Path.home())
        add(os.environ.get("GATE_TMPDIR") or "/var/tmp")
        if os.environ.get("TMPDIR"):
            add(str(Path(os.environ["TMPDIR"]) / "gate"))
        state = os.environ.get("XDG_STATE_HOME", "")
        add(str((Path(state) if Path(state).is_absolute() else home / ".local" / "state") / "gate"))
        cache = os.environ.get("XDG_CACHE_HOME", "")
        add(str((Path(cache) if Path(cache).is_absolute() else home / ".cache") / "gate"))
    uv_cache = os.environ.get("UV_CACHE_DIR")
    if uv_cache:
        add(uv_cache)
    elif shutil.which("uv"):
        add(tool_out(["uv", "cache", "dir"]))
    if shutil.which("uv"):
        # uvx stages its temp files in the tool dir.
        add(os.environ.get("UV_TOOL_DIR") or tool_out(["uv", "tool", "dir"]))
    if shutil.which("cargo"):
        # Registry, git checkouts and cargo-deny's advisory DBs only: ~/.cargo/bin stays read-only.
        cargo_home = Path(
            os.environ.get("CARGO_HOME") or Path(os.environ.get("HOME") or Path.home()) / ".cargo"
        )
        for sub in ("registry", "git", "advisory-dbs"):
            add(str(cargo_home / sub))
    return found


def _host_tmp_reads(cwd: str, path_env: str) -> list[str]:
    """Host ``/tmp`` paths the command still reads under the tmpfs: PATH tools, and the tree above *cwd*."""
    tmp = Path("/tmp")  # noqa: S108
    reads = [
        p for p in path_env.split(os.pathsep) if p and tmp in Path(p).parents and Path(p).is_dir()
    ]
    cwd_path = Path(cwd)
    if tmp in cwd_path.parents:
        reads.append(str(tmp / cwd_path.relative_to(tmp).parts[0]))
    return list(dict.fromkeys(reads))


def _checkout_root(cwd: Path) -> Path:
    """The checkout holding *cwd*: an install in a workspace member writes the root lockfile."""
    return next((d for d in (cwd, *cwd.parents) if (d / ".git").exists()), cwd)


def _sandboxed_argv(cmd: list[str], cwd: Path, path_env: str = "") -> list[str]:
    """Wrap *cmd* in bubblewrap: repo writable, ``/`` and ``$HOME`` read-only.

    Tool caches are writable binds. ``/tmp`` is a tmpfs. Network stays shared.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        msg = (
            "bwrap not found; install bubblewrap to run repo verify and builds "
            "(refusing to run unsandboxed)"
        )
        raise FileNotFoundError(msg)
    home = os.environ.get("HOME") or str(Path.home())
    cwd_s = str(_checkout_root(cwd.resolve()))
    caches = _tool_cache_dirs()
    isolated = list(
        dict.fromkeys(str(Path(p).parent) for p in _ISOLATED_ENV.values() if Path(p).exists())
    )
    argv = [
        bwrap,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",  # noqa: S108
        "--die-with-parent",
    ]
    # tmpfs hides host /tmp; recreate dest parents so later binds succeed.
    tmp_reads = _host_tmp_reads(cwd_s, path_env)
    argv.extend(_tmp_parent_dirs([home, cwd_s, *tmp_reads, *caches, *isolated]))
    # Overlay, not a bind, for host /tmp reads: files show through, writes still land in the tmpfs.
    mounts = [(p, ["--overlay-src", p, "--tmp-overlay", p]) for p in tmp_reads]
    mounts.append((home, ["--ro-bind", home, home]))
    mounts += [(p, ["--bind", p, p]) for p in [*caches, cwd_s, *isolated]]
    # Parents first, so the more specific mount wins wherever one path holds another.
    for _, flags in sorted(mounts, key=lambda m: len(Path(m[0]).parts)):
        argv.extend(flags)
    argv.append("--")
    argv.extend(cmd)
    return argv


def _tmp_parent_dirs(paths: list[str]) -> list[str]:
    flags: list[str] = []
    seen: set[str] = set()
    tmp = Path("/tmp")  # noqa: S108
    for raw in paths:
        path = Path(raw)
        if tmp not in path.parents:
            continue
        for parent in reversed(path.parents):
            if parent in {tmp, Path("/")}:
                continue
            if tmp not in parent.parents:
                continue
            key = str(parent)
            if key in seen:
                continue
            seen.add(key)
            flags.extend(["--dir", key])
    return flags


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
    if env:
        merged.update(env)
    if cmd[:1] == ["git"]:
        merged["GIT_LITERAL_PATHSPECS"] = "1"
        argv = cmd
    else:
        # Isolation wins over a verify/apply env that names the operator's files.
        merged.update(_ISOLATED_ENV)
        # The user namespace shows root-owned /etc/ssh as nobody's, which ssh refuses; -F skips it.
        ssh_config = Path(merged.get("HOME") or Path.home()) / ".ssh" / "config"
        merged.setdefault(
            "GIT_SSH_COMMAND", f"ssh -F {ssh_config if ssh_config.is_file() else os.devnull}"
        )
        argv = _sandboxed_argv(cmd, cwd_path, merged.get("PATH", ""))
    proc = subprocess.run(
        argv,
        cwd=str(cwd_path),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=merged,
        input=stdin,
    )
    return proc
