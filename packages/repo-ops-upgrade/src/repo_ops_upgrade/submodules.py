"""Git submodule discovery, ordering, and post-apply pointer hints."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from repo_ops_core.git import git_repo_for, run_git

SubmoduleOrder = str  # "discovery" | "children-first"


@dataclass(frozen=True)
class SubmoduleLink:
    """One submodule entry under a parent git repo."""

    path: str
    git_repo: Path
    parent: Path

    def to_json(self) -> dict[str, str]:
        return {
            "path": self.path,
            "git_repo": str(self.git_repo.resolve()),
            "parent": str(self.parent.resolve()),
        }


def _find_gitmodules(root: Path, *, max_depth: int = 32) -> list[Path]:
    """Walk for .gitmodules with depth limit and symlink-cycle guard."""
    found: list[Path] = []
    seen_roots: set[str] = set()
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            real = os.path.realpath(current)
        except OSError:
            continue
        if real in seen_roots:
            continue
        seen_roots.add(real)
        if depth > max_depth:
            continue
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name == ".gitmodules" and entry.is_file():
                found.append(entry)
            elif entry.is_dir() and not entry.is_symlink():
                stack.append((entry, depth + 1))
    return sorted(found)


def _parse_gitmodules_file(gitmodules_path: Path) -> list[tuple[str, str]]:
    """Return (path, url) pairs from a .gitmodules file."""
    parser = configparser.ConfigParser()
    parser.read(gitmodules_path, encoding="utf-8")
    entries: list[tuple[str, str]] = []
    for section in parser.sections():
        if not section.startswith("submodule "):
            continue
        path = (parser.get(section, "path", fallback="") or "").strip()
        url = (parser.get(section, "url", fallback="") or "").strip()
        if path:
            entries.append((path, url))
    return entries


def discover_submodules_under_roots(
    roots: list[str], *, flat: bool
) -> list[SubmoduleLink]:
    """Find .gitmodules under scanned roots and resolve parent/child git repos."""
    links: list[SubmoduleLink] = []
    seen: set[tuple[str, str, str]] = set()

    for root_s in roots:
        root = Path(root_s).expanduser().resolve()
        if not root.is_dir():
            continue
        candidates: list[Path]
        if flat and len(roots) == 1:
            gm = root / ".gitmodules"
            candidates = [gm] if gm.is_file() else []
        else:
            candidates = _find_gitmodules(root)

        for gitmodules_path in candidates:
            parent = git_repo_for(gitmodules_path.parent)
            if parent is None:
                continue
            parent = parent.resolve()
            for rel_path, _url in _parse_gitmodules_file(gitmodules_path):
                child_dir = (parent / rel_path).resolve()
                child_repo = git_repo_for(child_dir)
                if child_repo is None:
                    continue
                key = (str(parent), rel_path, str(child_repo.resolve()))
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    SubmoduleLink(
                        path=rel_path.replace("\\", "/"),
                        git_repo=child_repo.resolve(),
                        parent=parent,
                    )
                )
    return links


def submodules_to_json(links: list[SubmoduleLink]) -> list[dict[str, str]]:
    return [link.to_json() for link in links]


def sort_bundles_children_first(
    bundle_keys: list[Path],
    links: list[SubmoduleLink],
) -> list[Path]:
    """Topological order: child git repos before parents that embed them."""
    if not links:
        return bundle_keys

    key_set = set(bundle_keys)
    depth: dict[Path, int] = {k: 0 for k in bundle_keys}

    changed = True
    while changed:
        changed = False
        for link in links:
            if link.git_repo not in key_set or link.parent not in key_set:
                continue
            parent_depth = depth[link.git_repo] + 1
            if parent_depth > depth[link.parent]:
                depth[link.parent] = parent_depth
                changed = True

    return sorted(bundle_keys, key=lambda k: (depth.get(k, 0), str(k)))


def parent_repos_needing_pointer_commit(
    links: list[SubmoduleLink],
) -> list[dict[str, str]]:
    """Parents whose submodule gitlinks differ between index and HEAD/worktree."""
    hints: list[dict[str, str]] = []
    parents: dict[Path, list[str]] = {}
    for link in links:
        parents.setdefault(link.parent, []).append(link.path)

    for parent, paths in sorted(parents.items(), key=lambda item: str(item[0])):
        dirty_paths = _dirty_submodule_paths(parent, paths)
        if not dirty_paths:
            continue
        hints.append(
            {
                "parent": str(parent),
                "paths": ",".join(dirty_paths),
                "hint": (
                    "Submodule pointer(s) changed — commit parent after child "
                    "repos are pushed (git-push-order: children first)."
                ),
            }
        )
    return hints


def _dirty_submodule_paths(parent: Path, submodule_paths: list[str]) -> list[str]:
    proc = run_git(parent, "status", "--porcelain", timeout=30)
    if proc.returncode != 0:
        return []
    dirty: list[str] = []
    path_set = {p.replace("\\", "/") for p in submodule_paths}
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        # Submodule gitlink changes often appear as " M path" or "M  path"
        if entry in path_set or any(
            entry == p or entry.endswith(f"/{p}") for p in path_set
        ):
            dirty.append(entry)
            continue
        for p in path_set:
            if entry == p or entry.startswith(f"{p}/"):
                dirty.append(p)
                break
    # Also catch '+' from `git submodule status` (checkout ahead of index)
    sub_proc = run_git(parent, "submodule", "status", timeout=60)
    if sub_proc.returncode == 0:
        for line in sub_proc.stdout.splitlines():
            if not line or line[0] not in "+U":
                continue
            rest = line[1:].strip()
            parts = rest.split()
            if len(parts) < 2:
                continue
            sub_path = parts[1].replace("\\", "/")
            if sub_path in path_set and sub_path not in dirty:
                dirty.append(sub_path)
    return sorted(set(dirty))
