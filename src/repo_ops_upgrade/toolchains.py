"""Toolchain pins (Go, Rust, Python, Node) moved to the latest stable release.

Packages are the ecosystem plugins' job; this covers the version a repo pins for
the toolchain itself. A pin only ever rises, keeps its own precision (``1.27``
stays two-part and only moves when the minor does), and obeys ``[[hold]]``
rules named after the tool: ``go``, ``rust``, ``python``, ``node``. MSRV and
``requires-python`` floors are never touched. A failed fetch is a warning and
leaves the pin alone.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_core.http import open_url
from repo_ops_core.version_utils import semver_tuple
from repo_ops_go.go import go_work_dir_for
from repo_ops_models import Change, Workspace

logger = logging.getLogger(__name__)

_GO_WORK_NAME = "go.work go directive"


@functools.cache
def _fetch(url: str) -> str | None:
    try:
        with open_url(url, timeout=TIMEOUT_CHECK) as resp:
            return str(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("toolchain lookup failed for %s: %s", url, exc)
        return None


def _json_list(url: str) -> list[dict]:
    text = _fetch(url)
    try:
        doc = json.loads(text) if text else None
    except ValueError:
        return []
    return [e for e in doc if isinstance(e, dict)] if isinstance(doc, list) else []


def _latest_go(_current: str) -> str | None:
    for rel in _json_list("https://go.dev/dl/?mode=json"):
        version = str(rel.get("version", ""))
        if rel.get("stable") is True and version.startswith("go"):
            return version.removeprefix("go")
    return None


def _latest_rust(_current: str) -> str | None:
    m = re.search(
        r"\[pkg\.rust\]\s*\nversion = \"(\d+\.\d+\.\d+)",
        _fetch("https://static.rust-lang.org/dist/channel-rust-stable.toml") or "",
    )
    return m.group(1) if m else None


def _latest_python(_current: str) -> str | None:
    found = [
        str(e["latest"])
        for e in _json_list("https://endoflife.date/api/python.json")
        if re.fullmatch(r"\d+\.\d+\.\d+", str(e.get("latest", "")))
    ]
    return max(found, key=semver_tuple, default=None)


def _latest_node(current: str) -> str | None:
    # Same major only: a new major is a runtime migration, not a pin refresh.
    major = semver_tuple(current)[0]
    found = [
        v
        for e in _json_list("https://nodejs.org/dist/index.json")
        if (v := str(e.get("version", "")).removeprefix("v")) and semver_tuple(v)[0] == major
    ]
    return max(found, key=semver_tuple, default=None)


@dataclass(frozen=True)
class _Pin:
    tool: str
    label: str
    path: Path
    regex: re.Pattern[str]
    latest: Callable[[str], str | None]
    # go.work follows the module's resulting version, not the registry.
    target_override: str | None = None


_VER = r"(?P<v>\d+\.\d+(?:\.\d+)?)"
_GO_RE = re.compile(rf"^(?P<pre>go\s+){_VER}$", re.MULTILINE)
_GO_TOOLCHAIN_RE = re.compile(rf"^(?P<pre>toolchain\s+go){_VER}$", re.MULTILINE)
_RUST_RE = re.compile(r'^(?P<pre>\s*channel\s*=\s*")' + _VER + r'(?=")', re.MULTILINE)
_RUST_PLAIN_RE = re.compile(rf"\A(?P<pre>\s*){_VER}(?=\s*\Z)")
_PY_RE = re.compile(rf"\A(?P<pre>\s*){_VER}(?=\s*\Z)")
_NODE_RE = re.compile(rf"\A(?P<pre>\s*v?){_VER}(?=\s*\Z)")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _current(pin: _Pin) -> str | None:
    text = _read(pin.path)
    m = pin.regex.search(text) if text is not None else None
    return m.group("v") if m else None


def _pins(ws: Workspace) -> list[_Pin]:
    p = ws.path
    if ws.ecosystem == "go":
        return [
            _Pin("go", "go", p / "go.mod", _GO_RE, _latest_go),
            _Pin("go", "go toolchain", p / "go.mod", _GO_TOOLCHAIN_RE, _latest_go),
        ]
    if ws.ecosystem == "rust":
        return [
            _Pin("rust", "rust", p / "rust-toolchain.toml", _RUST_RE, _latest_rust),
            _Pin("rust", "rust", p / "rust-toolchain", _RUST_PLAIN_RE, _latest_rust),
        ]
    if ws.ecosystem == "py":
        return [_Pin("python", "python", p / ".python-version", _PY_RE, _latest_python)]
    if ws.ecosystem == "node":
        return [
            _Pin("node", "node", p / name, _NODE_RE, _latest_node)
            for name in (".nvmrc", ".node-version")
        ]
    return []


def _newer(target: str, current: str) -> bool:
    """Whether *target* is above *current*, compared at *current*'s precision."""
    n = len(current.split("."))
    return semver_tuple(target)[:n] > semver_tuple(current)[:n]


def _written(target: str, current: str) -> str:
    return ".".join(target.split(".")[: len(current.split("."))])


def _go_work_pins(ws: Workspace, module_version: str) -> list[_Pin]:
    work_dir = go_work_dir_for(ws.path)
    if work_dir is None:
        return []
    path = work_dir / "go.work"
    return [
        _Pin("go", _GO_WORK_NAME, path, _GO_RE, _latest_go, target_override=module_version),
    ]


def _resolve(ws: Workspace, pins: list[_Pin]) -> tuple[list[tuple[_Pin, str, str]], list[Change]]:
    """Split pins into ``(pin, current, new)`` moves and held-back ``behind`` rows."""
    moves: list[tuple[_Pin, str, str]] = []
    behind: list[Change] = []
    for pin in pins:
        cur = _current(pin)
        if cur is None:
            continue
        target = pin.target_override or pin.latest(cur)
        if target is None or not _newer(target, cur):
            continue
        if not target_within_hold(target, resolve_package_hold(ws, pin.tool)):
            behind.append(Change(pin.label, cur, _written(target, cur)))
            continue
        moves.append((pin, cur, _written(target, cur)))
    return moves, behind


def _plan(ws: Workspace) -> tuple[list[tuple[_Pin, str, str]], list[Change]]:
    moves, behind = _resolve(ws, _pins(ws))
    if ws.ecosystem == "go":
        # go.work must not stay below a module it lists, whether that module
        # just moved or already sits above it.
        module = next((m for m in moves if m[0].label == "go"), None)
        base = _current(_pins(ws)[0])
        version = module[2] if module else base
        if version:
            more, more_behind = _resolve(ws, _go_work_pins(ws, version))
            moves += more
            behind += more_behind
    return moves, behind


def plan_toolchains(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """``(changes, behind_latest)`` for the toolchain pins in *ws*; writes nothing."""
    moves, behind = _plan(ws)
    return [Change(pin.label, cur, new) for pin, cur, new in moves], behind


def apply_toolchains(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """Rewrite the pins :func:`plan_toolchains` reports; same return shape."""
    moves, behind = _plan(ws)
    for pin, _, new in moves:
        text = _read(pin.path)
        if text is None:
            continue
        pin.path.write_text(
            pin.regex.sub(lambda m, new=new: m.group("pre") + new, text, count=1),
            encoding="utf-8",
        )
    return [Change(pin.label, cur, new) for pin, cur, new in moves], behind


def merge_toolchains(
    changes: list[Change], behind: list[Change], more: list[Change], more_behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Append toolchain rows, replacing same-named rows the ecosystem plugin already made."""
    names = {c.name for c in more}
    return (
        [c for c in changes if c.name not in names] + more,
        [c for c in behind if c.name not in {b.name for b in more_behind}] + more_behind,
    )
