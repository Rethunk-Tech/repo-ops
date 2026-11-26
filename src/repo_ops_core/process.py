"""Subprocess helpers shared by repo-ops commands."""

from __future__ import annotations

import atexit
import json
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


def _gate_isolate_env() -> dict[str, str]:
    """The toolchain caches gate's --isolate keeps visible (`gate isolate-env`): the one list, owned by gate."""
    if not shutil.which("gate"):
        return {}
    try:
        proc = subprocess.run(
            ["gate", "isolate-env"], capture_output=True, text=True, timeout=30, check=False
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return (
        {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(data, dict)
        else {}
    )


def _tool_cache_dirs() -> list[str]:
    """Host paths that builds need to write: gate's isolate caches (sumdb, cargo and dotnet narrowed), uv tools, ccache and gate's own dirs."""
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

    gate_caches = _gate_isolate_env()
    for var, value in gate_caches.items():
        if var == "GOPATH":
            # The checksum DB cache that `go get` and `go mod tidy` write; the rest of GOPATH stays read-only.
            add(str(Path(value.split(os.pathsep)[0]) / "pkg" / "sumdb"))
        elif var == "CARGO_HOME":
            # Registry, git checkouts and cargo-deny's advisory DBs only: ~/.cargo/bin stays read-only.
            for sub in ("registry", "git", "advisory-dbs"):
                add(str(Path(value) / sub))
        elif var == "DOTNET_CLI_HOME":
            # gate's value is a HOME; the CLI keeps its global tools in .dotnet beneath it.
            add(str(Path(value) / ".dotnet"))
        else:
            add(value)
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
    if shutil.which("uv"):
        # uvx stages its temp files in the tool dir.
        add(os.environ.get("UV_TOOL_DIR") or tool_out(["uv", "tool", "dir"]))
    home = Path(os.environ.get("HOME") or Path.home())
    # The cgo compiler cache is not one of gate's isolate caches.
    add(os.environ.get("CCACHE_DIR") or str(home / ".cache" / "ccache"))
    return found


def _sweep_stale_runtime_dirs(root: Path) -> None:
    """Remove ``repo-ops-<pid>`` dirs left by runs that no longer exist."""
    for d in root.glob("repo-ops-*"):
        pid = d.name.removeprefix("repo-ops-")
        if pid.isdigit() and not Path("/proc", pid).exists():
            shutil.rmtree(d, ignore_errors=True)


def _runtime_dir_mount() -> list[str]:
    """Bind a per-run subdir over ``$XDG_RUNTIME_DIR`` so `go test -race` sockets work without exposing the real one."""
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if not raw or not Path(raw).is_dir():
        return []
    sub = Path(raw) / f"repo-ops-{os.getpid()}"
    if not sub.is_dir():
        _sweep_stale_runtime_dirs(Path(raw))
        try:
            sub.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            return []
        atexit.register(shutil.rmtree, sub, ignore_errors=True)
    flags = ["--bind", str(sub), raw]
    # gate opens its own per-run memory scope through this socket; without it every capped run goes uncapped.
    bus = Path(raw) / "systemd" / "private"
    if bus.is_socket():
        flags += ["--bind", str(bus), str(bus)]
    return flags


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


def _unescape_mount(raw: str) -> str:
    return (
        raw.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _on_overlay(path: str) -> bool:
    """True when *path* already sits on overlayfs (nested ``--tmp-overlay`` then fails)."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    best = -1
    found = False
    try:
        lines = Path("/proc/self/mounts").read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint = Path(_unescape_mount(parts[1]))
        if resolved != mountpoint and mountpoint not in resolved.parents:
            continue
        n = len(mountpoint.parts)
        if n > best:
            best = n
            found = parts[2] == "overlay"
    return found


def _throwaway_mount(path: str) -> list[str]:
    if _on_overlay(path):
        return ["--bind", path, path]
    return ["--overlay-src", path, "--tmp-overlay", path]


def _sandboxed_argv(cmd: list[str], cwd: Path, path_env: str = "") -> list[str]:
    """Wrap *cmd* in bubblewrap: repo writable, ``/`` read-only, ``$HOME`` a throwaway overlay.

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
        # pasta (nested network namespaces) opens the tap device; a fresh /dev lacks it.
        "--dev-bind-try",
        "/dev/net/tun",
        "/dev/net/tun",
        # A fresh procfs lets a test's own bwrap write its uid map; the host /proc is read-only.
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",  # noqa: S108
        # The user namespace shows root-owned includes as nobody's and ssh refuses them, even for
        # an ssh a test runs with its own -i/-o and no -F.
        "--tmpfs",
        "/etc/ssh/ssh_config.d",
        "--die-with-parent",
    ]
    # tmpfs hides host /tmp; recreate dest parents so later binds succeed.
    tmp_reads = _host_tmp_reads(cwd_s, path_env)
    argv.extend(_tmp_parent_dirs([home, cwd_s, *tmp_reads, *caches, *isolated]))
    # Overlay, not a bind, for host /tmp reads: files show through, writes still land in the tmpfs.
    # A nested run (apply wrapping gate wrapping run) cannot overlay an overlay: bind instead.
    mounts = [(p, _throwaway_mount(p)) for p in tmp_reads]
    # Throwaway overlay: tests may write under $HOME; the writes vanish with the run.
    mounts.append((home, _throwaway_mount(home)))
    mounts += [(p, ["--bind", p, p]) for p in [*caches, cwd_s, *isolated]]
    runtime = _runtime_dir_mount()
    if runtime:
        mounts.append((runtime[2], runtime))
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
        if cmd[:1] == ["uv"]:
            # UV_CONFIG_FILE switches off discovery of the repo's own uv.toml
            # (index, torch-backend); uv never writes config, so nothing to isolate.
            merged.pop("UV_CONFIG_FILE", None)
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
