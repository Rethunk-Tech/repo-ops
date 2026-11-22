"""Toolchain pins (Go, Rust, Python, Node, uv) moved to the latest stable release.

Packages are the ecosystem plugins' job; this covers the version a repo pins for
the toolchain itself. A pin only ever rises, keeps its own precision (``1.27``
stays two-part and only moves when the minor does), and obeys ``[[hold]]``
rules named after the tool: ``go``, ``rust``, ``python``, ``node``, ``uv``. MSRV
and ``requires-python`` floors are never touched. A failed fetch is a warning and
leaves the pin alone.

Version inputs in a workflow (``go-version:``, ``python-version:``,
``node-version:``, ``uv-version:``, and ``version:`` on a setup-uv step) are
toolchain pins a repo states in YAML instead of a manifest, so they move too.
So does the gate a workflow installs: ``go install .../cmd/gate@vX`` (also as a
commit SHA with a ``# vX`` comment) and the ``releases/download/vX/`` curl form.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_core.filesystem import guard_tool_writes, guarded_write
from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_core.http import open_url
from repo_ops_core.version_utils import semver_tuple
from repo_ops_go.go import go_work_dir_for, tidy_go_module
from repo_ops_models import Change, Workspace
from repo_ops_py.pypi import pypi_latest
from repo_ops_upgrade.actions import discover_workflow_files

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


@functools.cache
def _local_go_version() -> str | None:
    """Installed toolchain from ``go env GOVERSION``, or None if go is missing."""
    try:
        proc = subprocess.run(
            ["go", "env", "GOVERSION"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_CHECK,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    ver = proc.stdout.strip().removeprefix("go")
    return ver or None


def _latest_go(_current: str) -> str | None:
    latest = None
    for rel in _json_list("https://go.dev/dl/?mode=json"):
        version = str(rel.get("version", ""))
        if rel.get("stable") is True and version.startswith("go"):
            latest = version.removeprefix("go")
            break
    if latest is None:
        return None
    local = _local_go_version()
    if local is None:
        return latest
    return local if semver_tuple(local) < semver_tuple(latest) else latest


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


def _latest_node(_current: str) -> str | None:
    # Node pins follow the newest LTS, not the pin's own major: the index is
    # newest-first and only an LTS release carries the codename, so the first
    # truthy ``lts`` is the newest LTS and an odd Current major never wins.
    for entry in _json_list("https://nodejs.org/dist/index.json"):
        if not entry.get("lts"):
            continue
        version = str(entry.get("version", "")).removeprefix("v")
        if re.fullmatch(r"\d+\.\d+\.\d+", version):
            return version
    return None


def _latest_in_minor(current: str, versions: list[str]) -> str | None:
    """Highest of *versions* sharing the pin's major.minor."""
    minor = semver_tuple(current)[:2]
    return max(
        (v for v in versions if semver_tuple(v)[:2] == minor),
        key=semver_tuple,
        default=None,
    )


def _go_stable_versions() -> list[str]:
    return [
        version.removeprefix("go")
        for rel in _json_list("https://go.dev/dl/?mode=json")
        if rel.get("stable") is True
        and (version := str(rel.get("version", ""))).startswith("go")
        and re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version.removeprefix("go"))
    ]


def _latest_go_patch(current: str) -> str | None:
    """Newest go release in the pin's own minor; a bare major names no minor."""
    if len(semver_tuple(current)) < 2:
        return None
    return _latest_in_minor(current, _go_stable_versions())


def _latest_python_patch(current: str) -> str | None:
    """Newest CPython release in the pin's own minor."""
    if len(semver_tuple(current)) < 2:
        return None
    found = [
        str(e["latest"])
        for e in _json_list("https://endoflife.date/api/python.json")
        if re.fullmatch(r"\d+\.\d+\.\d+", str(e.get("latest", "")))
    ]
    return _latest_in_minor(current, found)


def _latest_uv(_current: str) -> str | None:
    return pypi_latest("uv")


# --- Workflow version inputs ------------------------------------------------
#
# A toolchain pinned in a workflow is the same pin as one in a manifest, so it
# moves under the same tool name: ``go``, ``python``, ``node``, ``uv``.

_INPUT_TOOLS = {
    "go-version": "go",
    "python-version": "python",
    "node-version": "node",
    "uv-version": "uv",
}
_INPUT_TOOLS_BY_ECOSYSTEM = {"go": ("go",), "py": ("python", "uv"), "node": ("node",)}
_SETUP_UV = "astral-sh/setup-uv"

_WS_KEY_RE = re.compile(
    r"^(?P<pre>[ \t]*(?:- )?)(?P<key>[A-Za-z0-9_-]+):[ \t]*"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"'#]*)(?P=quote)[ \t]*(?:#.*)?$"
)
_LITERAL_INPUT_RE = re.compile(r"\d+(?:\.\d+)*")


@dataclass(frozen=True)
class _Input:
    """One literal version input on one workflow line."""

    tool: str
    label: str
    path: Path
    line_no: int
    version: str
    # "go" or "url" for a gate install; empty for a version input.
    kind: str = ""


def _is_setup_uv(ref: str) -> bool:
    return ref.split("@", 1)[0].strip().lower() == _SETUP_UV


def _inputs_in(path: Path, lines: list[str], tools: tuple[str, ...]) -> list[_Input]:
    found: list[_Input] = []
    # Indents of the ``with:`` blocks still open at the current line; a step
    # input is only a pin inside one (a ``matrix:`` key of the same name is not).
    with_blocks: list[int] = []
    uses: str | None = None
    for index, line in enumerate(lines, 1):
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if body.startswith("- "):
            # A new step: the previous step's ``uses:`` must not carry over to
            # a ``with:`` block of its own.
            uses = None
        while with_blocks and indent <= with_blocks[-1]:
            with_blocks.pop()
        m = _WS_KEY_RE.match(line)
        if m is None:
            continue
        key = m.group("key")
        value = m.group("value")
        if key == "uses":
            uses = value
            continue
        if key == "with":
            with_blocks.append(indent)
            continue
        if not with_blocks or indent <= with_blocks[-1]:
            continue
        tool = _INPUT_TOOLS.get(key)
        if tool is None and key == "version" and uses is not None and _is_setup_uv(uses):
            tool = "uv"
        if tool not in tools:
            continue
        if not (_LITERAL_INPUT_RE.fullmatch(value) or (tool == "go" and value == "stable")):
            continue
        found.append(_Input(tool, "uv-version" if key == "version" else key, path, index, value))
    return found


_GATE_MODULE = "github.com/Rethunk-Tech/rethunk-gate-cli"
_GATE_GO_RE = re.compile(
    rf"{re.escape(_GATE_MODULE)}/cmd/gate@(?P<ref>v\d+\.\d+\.\d+|[0-9a-f]{{40}})"
    r"(?:[ \t]+#[ \t]*(?P<ctag>v\d+\.\d+\.\d+))?"
)
_GATE_URL_RE = re.compile(
    rf"{re.escape(_GATE_MODULE)}/releases/download/(?P<ref>v\d+\.\d+\.\d+)(?=/)"
)
_GATE_PROXY = "https://proxy.golang.org/github.com/!rethunk-!tech/rethunk-gate-cli"
_GATE_RELEASES = "https://api.github.com/repos/Rethunk-Tech/rethunk-gate-cli/releases/latest"


def _gate_inputs_in(path: Path, lines: list[str]) -> list[_Input]:
    found: list[_Input] = []
    for index, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        if m := _GATE_GO_RE.search(line):
            tag = m.group("ctag") or (m.group("ref") if m.group("ref").startswith("v") else None)
            if tag:
                found.append(_Input("gate", "gate", path, index, tag[1:], "go"))
        elif m := _GATE_URL_RE.search(line):
            found.append(_Input("gate", "gate", path, index, m.group("ref")[1:], "url"))
    return found


def _json_object(url: str) -> dict:
    text = _fetch(url)
    try:
        doc = json.loads(text) if text else None
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _gate_sha(version: str) -> str | None:
    origin = _json_object(f"{_GATE_PROXY}/@v/v{version}.info").get("Origin")
    sha = origin.get("Hash") if isinstance(origin, dict) else None
    return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _latest_gate(pin: _Input, line: str) -> str | None:
    """Newest gate release for the install form on *line*; a SHA pin needs a resolvable commit."""
    if pin.kind == "url":
        tag = _json_object(_GATE_RELEASES).get("tag_name")
    else:
        tag = _json_object(f"{_GATE_PROXY}/@latest").get("Version")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return None
    version = tag[1:]
    m = _GATE_GO_RE.search(line)
    if m and m.group("ctag") and _gate_sha(version) is None:
        return None
    return version


def _workflow_inputs(ws: Workspace) -> list[_Input]:
    tools = _INPUT_TOOLS_BY_ECOSYSTEM.get(ws.ecosystem, ())
    found: list[_Input] = []
    for wf in discover_workflow_files(ws.git_repo):
        try:
            lines = wf.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if tools:
            found.extend(_inputs_in(wf, lines, tools))
        found.extend(_gate_inputs_in(wf, lines))
    return found


def _input_target(tool: str, current: str) -> str | None:
    if tool == "go":
        if current == "stable":
            return max(_go_stable_versions(), key=semver_tuple, default=None)
        return _latest_go_patch(current)
    if tool == "python":
        return _latest_python_patch(current)
    if tool == "node":
        return _latest_node(current)
    return _latest_uv(current)


def _workflow_moves(ws: Workspace) -> tuple[list[tuple[_Input, str, str]], list[Change]]:
    moves: list[tuple[_Input, str, str]] = []
    behind: list[Change] = []
    for pin in _workflow_inputs(ws):
        if pin.tool == "gate":
            lines = (_read(pin.path) or "").splitlines()
            line = lines[pin.line_no - 1] if 0 < pin.line_no <= len(lines) else ""
            target = _latest_gate(pin, line)
        else:
            target = _input_target(pin.tool, pin.version)
        if target is None:
            continue
        if _LITERAL_INPUT_RE.fullmatch(pin.version):
            if not _newer(target, pin.version):
                continue
            new = _written(target, pin.version)
        else:
            new = target
        if not target_within_hold(new, resolve_package_hold(ws, pin.tool)):
            behind.append(Change(pin.label, pin.version, new))
            continue
        moves.append((pin, pin.version, new))
    return moves, behind


def _apply_gate_pin(line: str, pin: _Input, new: str) -> str | None:
    m = (_GATE_URL_RE if pin.kind == "url" else _GATE_GO_RE).search(line)
    if m is None:
        return None
    ref = m.group("ref")
    ctag = m.groupdict().get("ctag")
    if (ctag or ref).removeprefix("v") != pin.version:
        return None
    if ctag:
        sha = _gate_sha(new)
        if sha is None:
            return None
        line = line[: m.start("ctag")] + f"v{new}" + line[m.end("ctag") :]
        return line[: m.start("ref")] + sha + line[m.end("ref") :]
    return line[: m.start("ref")] + f"v{new}" + line[m.end("ref") :]


def _apply_workflow_input(pin: _Input, new: str, root: Path) -> bool:
    text = _read(pin.path)
    lines = text.splitlines(keepends=True) if text is not None else []
    index = pin.line_no - 1
    if not 0 <= index < len(lines):
        return False
    if pin.tool == "gate":
        body = lines[index].rstrip("\r\n")
        rewritten = _apply_gate_pin(body, pin, new)
        if rewritten is None:
            return False
        lines[index] = rewritten + lines[index][len(body) :]
        guarded_write(pin.path, "".join(lines).encode("utf-8"), root, appender="toolchains")
        return True
    m = _WS_KEY_RE.match(lines[index].rstrip("\r\n"))
    if m is None or m.group("value") != pin.version:
        return False
    lines[index] = lines[index][: m.start("value")] + new + lines[index][m.end("value") :]
    guarded_write(pin.path, "".join(lines).encode("utf-8"), root, appender="toolchains")
    return True


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
# ``engines.node`` is a pin only when it is exact; a ``>=``/``^``/``x`` spec is a
# floor for consumers and must keep working for older runtimes.
_NODE_ENGINES_RE = re.compile(
    r'(?P<pre>"engines"\s*:\s*\{[^{}]*?"node"\s*:\s*")(?P<v>\d+(?:\.\d+)*)(?=")'
)


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
        ] + [_Pin("node", "engines.node", p / "package.json", _NODE_ENGINES_RE, _latest_node)]
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


def _log_major_moves(rows: list[tuple[str, str, str]]) -> None:
    """Announce each pin crossing a major on its own line."""
    for label, cur, new in rows:
        if semver_tuple(new)[0] != semver_tuple(cur)[0]:
            logger.info("major move %s: %s → %s", label, cur, new)


def plan_toolchains(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """``(changes, behind_latest)`` for the toolchain pins in *ws*; writes nothing."""
    moves, behind = _plan(ws)
    input_moves, input_behind = _workflow_moves(ws)
    rows = moves + input_moves
    _log_major_moves([(pin.label, cur, new) for pin, cur, new in rows])
    return (
        [Change(pin.label, cur, new) for pin, cur, new in rows],
        behind + input_behind,
    )


def plan_toolchain_pins(ws: Workspace) -> tuple[list[tuple[_Pin, str, str]], list[Change]]:
    """The pin rewrites for :func:`apply_toolchains`, taken before the dependency apply.

    ``go get`` raises the go directive itself, so planning afterwards would size
    the written version from that raised pin instead of the one the repo had.
    """
    return _plan(ws)


def apply_toolchains(
    ws: Workspace,
    planned: tuple[list[tuple[_Pin, str, str]], list[Change]] | None = None,
) -> tuple[list[Change], list[Change]]:
    """Rewrite the pins :func:`plan_toolchains` reports; same return shape."""
    moves, behind = planned or _plan(ws)
    input_moves, input_behind = _workflow_moves(ws)
    # go.work may sit above the workspace (a repo-root work file for a module
    # below it) but never above the repo, which is what confines every write
    # and the rollback.
    if not guard_tool_writes(
        ws.git_repo,
        "toolchains",
        *(pin.path for pin, _, _ in moves),
        *(pin.path for pin, _, _ in input_moves),
    ):
        return [], behind + input_behind
    for pin, _, new in moves:
        text = _read(pin.path)
        if text is None:
            continue
        guarded_write(
            pin.path,
            pin.regex.sub(lambda m, new=new: m.group("pre") + new, text, count=1).encode("utf-8"),
            ws.git_repo,
            appender="toolchains",
        )
    for pin, _, new in input_moves:
        _apply_workflow_input(pin, new, ws.git_repo)
    if any(pin.path.name == "go.mod" for pin, _, _ in moves):
        # Past go 1.17 go.mod must list every module the build needs, so a
        # raised directive is only valid once tidy has filled them in.
        tidy_go_module(ws)
    rows = moves + input_moves
    _log_major_moves([(pin.label, cur, new) for pin, cur, new in rows])
    return (
        [Change(pin.label, cur, new) for pin, cur, new in rows],
        behind + input_behind,
    )


def merge_toolchains(
    changes: list[Change], behind: list[Change], more: list[Change], more_behind: list[Change]
) -> tuple[list[Change], list[Change]]:
    """Append toolchain rows, replacing same-named rows the ecosystem plugin already made."""
    names = {c.name for c in more}
    return (
        [c for c in changes if c.name not in names] + more,
        [c for c in behind if c.name not in {b.name for b in more_behind}] + more_behind,
    )
