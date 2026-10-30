"""NuGet (.NET) upgrade check/apply.

Versions are read from the project files rather than ``dotnet list package
--outdated``: that command ignores feeds a project adds through
``RestoreAdditionalProjectSources`` and never sees ``global.json``
``msbuild-sdks`` pins. Each bump is restored on its own and reverted when
restore fails, so a version a feed cannot serve never lands.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
from functools import cache
from pathlib import Path

from repo_ops_core.constants import SKIP_DIRS, TIMEOUT_APPLY, TIMEOUT_CHECK
from repo_ops_core.filesystem import restore_file, snapshot_file
from repo_ops_core.holds import resolve_package_hold, target_within_hold
from repo_ops_core.http import open_url
from repo_ops_core.process import run
from repo_ops_models import Change, Workspace

NUGET_ORG = "https://api.nuget.org/v3/index.json"
PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".props", ".targets")
SOLUTION_SUFFIXES = (".sln", ".slnx")
_LOCKFILE = "packages.lock.json"
_SKIP = SKIP_DIRS | {"bin", "obj", "artifacts"}
_DOTNET_ENV = {"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"}

_REF_TAG_RE = re.compile(
    r"<(?:PackageReference|PackageVersion|GlobalPackageReference)\b[^>]*>", re.S
)
_ATTR_RE = r"""\b{name}\s*=\s*(["'])(.*?)\1"""
_SOURCES_RE = re.compile(
    r"<(RestoreAdditionalProjectSources|RestoreSources)\b[^>]*>(.*?)</\1>", re.S
)
_CONFIG_SOURCE_RE = re.compile(r"""<add\b[^>]*\bvalue\s*=\s*["'](https?://[^"']+)["']""", re.I)
# A literal NuGet version; ranges, wildcards and $(Property) references are left to their owner.
_LITERAL_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}(?:-[0-9A-Za-z.-]+)?$")


def _attr(tag: str, name: str) -> re.Match[str] | None:
    return re.search(_ATTR_RE.format(name=name), tag)


def _walk(mdir: Path) -> list[tuple[Path, list[str]]]:
    """Directories under *mdir* with their file names, skipping build output and nested repos."""
    out: list[tuple[Path, list[str]]] = []
    for dirpath, dirnames, filenames in os.walk(mdir):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in _SKIP and not (here / d / ".git").exists()]
        out.append((here, filenames))
    return out


def find_nuget_files(mdir: Path) -> list[Path]:
    """Every file a NuGet apply may rewrite: project files, ``global.json`` and lock files."""
    return sorted(
        here / name
        for here, names in _walk(mdir)
        for name in names
        if name.endswith(PROJECT_SUFFIXES) or name in (_LOCKFILE, "global.json")
    )


def is_nuget_root(names: set[str]) -> bool:
    return any(n.endswith(SOLUTION_SUFFIXES + PROJECT_SUFFIXES[:3]) for n in names)


def _project_files(mdir: Path) -> list[Path]:
    return [p for p in find_nuget_files(mdir) if p.name.endswith(PROJECT_SUFFIXES)]


def _references(text: str) -> list[tuple[str, str]]:
    """(package id, literal version) pairs a project file pins."""
    refs: list[tuple[str, str]] = []
    for tag in _REF_TAG_RE.findall(text):
        include = _attr(tag, "Include")
        version = _attr(tag, "Version")
        if include and version and _LITERAL_VERSION_RE.match(version.group(2)):
            refs.append((include.group(2), version.group(2)))
    return refs


def _sdk_pins(global_json: Path) -> dict[str, str]:
    try:
        document = json.loads(global_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sdks = document.get("msbuild-sdks") if isinstance(document, dict) else None
    if not isinstance(sdks, dict):
        return {}
    return {
        name: version
        for name, version in sdks.items()
        if isinstance(version, str) and _LITERAL_VERSION_RE.match(version)
    }


def _pins(mdir: Path) -> list[tuple[str, str]]:
    pins: set[tuple[str, str]] = set()
    for path in find_nuget_files(mdir):
        if path.name.endswith(PROJECT_SUFFIXES):
            pins.update(_references(path.read_text(encoding="utf-8", errors="replace")))
        elif path.name == "global.json":
            pins.update(_sdk_pins(path).items())
    return sorted(pins, key=lambda p: (p[0].lower(), p[1]))


def _feeds(ws: Workspace) -> tuple[str, ...]:
    """nuget.org plus every http(s) feed the projects or a NuGet.config up to the repo root add."""
    feeds = [NUGET_ORG]
    for path in _project_files(ws.path):
        for _, body in _SOURCES_RE.findall(path.read_text(encoding="utf-8", errors="replace")):
            feeds.extend(s.strip() for s in body.split(";") if s.strip().startswith("http"))
    current = ws.path.resolve()
    ceiling = ws.git_repo.resolve()
    while True:
        for entry in current.iterdir():
            if entry.is_file() and entry.name.lower() == "nuget.config":
                feeds.extend(_CONFIG_SOURCE_RE.findall(entry.read_text(errors="replace")))
        if current in (ceiling, current.parent):
            break
        current = current.parent
    return tuple(dict.fromkeys(feeds))


def _get_json(url: str) -> object:
    with open_url(url, timeout=TIMEOUT_CHECK, headers={"User-Agent": "repo-ops"}) as response:
        return json.loads(response.read().decode("utf-8"))


@cache
def _package_base(feed: str) -> str:
    document = _get_json(feed)
    resources = document.get("resources") if isinstance(document, dict) else None
    for resource in resources if isinstance(resources, list) else []:
        if isinstance(resource, dict) and str(resource.get("@type", "")).startswith(
            "PackageBaseAddress/3.0.0"
        ):
            return str(resource["@id"]).rstrip("/")
    raise RuntimeError(f"NuGet feed {feed} has no PackageBaseAddress resource")


@cache
def _feed_versions(feed: str, package: str) -> tuple[str, ...]:
    # ponytail: the flat container lists unlisted versions too; restore still
    # resolves them, so this only matters if a publisher unlists a bad release.
    url = f"{_package_base(feed)}/{package.lower()}/index.json"
    try:
        document = _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ()
        raise RuntimeError(f"NuGet lookup of {package} on {feed} failed: HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"NuGet lookup of {package} on {feed} failed: {exc}") from exc
    versions = document.get("versions") if isinstance(document, dict) else None
    return tuple(v for v in versions or () if isinstance(v, str))


def nuget_version_key(
    version: str,
) -> tuple[tuple[int, ...], bool, tuple[tuple[int, int, str], ...]]:
    """NuGet precedence: up to four numeric parts, then a release above its prereleases."""
    core, _, pre = version.strip().split("+")[0].partition("-")
    nums = tuple(int(p) for p in core.split(".") if p.isdigit())
    ids = tuple(
        (0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in pre.split(".") if pre
    )
    return (*nums, 0, 0, 0, 0)[:4], not pre, ids


def _latest(ws: Workspace, package: str, current: str) -> str | None:
    # A prerelease is only a target for a pin that already is one.
    opted_in = "-" in current
    versions = {
        v for feed in _feeds(ws) for v in _feed_versions(feed, package) if opted_in or "-" not in v
    }
    return max(versions, key=nuget_version_key, default=None)


def _candidates(ws: Workspace) -> tuple[list[Change], list[Change]]:
    """(bumps a hold allows, bumps a hold blocks) for every outdated pin."""
    allowed: list[Change] = []
    held: list[Change] = []
    for package, current in _pins(ws.path):
        latest = _latest(ws, package, current)
        if latest is None or nuget_version_key(latest) <= nuget_version_key(current):
            continue
        change = Change(package, current, latest)
        rule = resolve_package_hold(ws, package)
        if target_within_hold(latest, rule):
            allowed.append(change)
            continue
        within = [
            v
            for feed in _feeds(ws)
            for v in _feed_versions(feed, package)
            if ("-" in current or "-" not in v) and target_within_hold(v, rule)
        ]
        best = max(within, key=nuget_version_key, default=None)
        if best is not None and nuget_version_key(best) > nuget_version_key(current):
            allowed.append(Change(package, current, best))
        held.append(change)
    return allowed, held


def _rewrite(path: Path, package: str, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if path.name == "global.json":
        updated = re.sub(
            rf'("{re.escape(package)}"\s*:\s*")' + re.escape(old) + '(")',
            lambda m: f"{m.group(1)}{new}{m.group(2)}",
            text,
        )
    else:

        def bump(match: re.Match[str]) -> str:
            tag = match.group(0)
            include = _attr(tag, "Include")
            version = _attr(tag, "Version")
            if not (include and version) or include.group(2).lower() != package.lower():
                return tag
            if version.group(2) != old:
                return tag
            return tag[: version.start(2)] + new + tag[version.end(2) :]

        updated = _REF_TAG_RE.sub(bump, text)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _restore_targets(mdir: Path) -> list[Path]:
    entries = sorted(p for p in mdir.iterdir() if p.is_file())
    solutions = [p for p in entries if p.name.endswith(SOLUTION_SUFFIXES)]
    return solutions or [p for p in entries if p.name.endswith(PROJECT_SUFFIXES[:3])]


def _restore(ws: Workspace) -> str | None:
    for target in _restore_targets(ws.path):
        proc = run(
            ["dotnet", "restore", target.name, "--force-evaluate"],
            ws.path,
            timeout=TIMEOUT_APPLY,
            env=_DOTNET_ENV,
        )
        if proc.returncode != 0:
            return (proc.stdout + proc.stderr).strip() or f"dotnet restore {target.name} failed"
    return None


def check_nuget(ws: Workspace) -> tuple[list[Change], list[Change]]:
    return _candidates(ws)


def apply_nuget(ws: Workspace) -> list[Change]:
    """Bump each pin and restore; return the bumps that did not land plus the held ones."""
    allowed, held = _candidates(ws)
    remaining = list(held)
    for change in allowed:
        files = find_nuget_files(ws.path)
        snapshots = {path: snapshot_file(path) for path in files}
        touched = [
            path
            for path in files
            if path.name != _LOCKFILE and _rewrite(path, change.name, change.from_ver, change.to)
        ]
        if touched and _restore(ws) is None:
            continue
        for path, snapshot in snapshots.items():
            restore_file(path, snapshot)
        remaining.append(change)
    return remaining


def nuget_snapshot_changes(before: dict[Path, bytes], after: dict[Path, bytes]) -> list[Change]:
    """Per-package changes between two snapshots of :func:`find_nuget_files`."""

    def pins(snapshot: dict[Path, bytes]) -> dict[str, str]:
        out: dict[str, str] = {}
        for path, data in snapshot.items():
            text = data.decode("utf-8", errors="replace")
            if path.name.endswith(PROJECT_SUFFIXES):
                out.update(_references(text))
            elif path.name == "global.json":
                try:
                    sdks = json.loads(text).get("msbuild-sdks") or {}
                except (ValueError, AttributeError):
                    sdks = {}
                out.update({k: v for k, v in sdks.items() if isinstance(v, str)})
        return out

    b, a = pins(before), pins(after)
    return [
        Change(name, b.get(name, "?"), ver) for name, ver in sorted(a.items()) if b.get(name) != ver
    ]
