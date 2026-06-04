"""Workspace discovery and repo grouping for upgrades."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_core.discovery import discover_repos
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.git import git_repo_for, repo_label
from repo_ops_core.walk import iter_scanned_dirs
from repo_ops_js.workspaces import (
    detect_node_manager,
    path_is_yarn_workspace_child,
    yarn_workspace_members,
)
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_py_uv.uv import path_is_uv_workspace_child, uv_workspace_members

from repo_ops_upgrade.actions import discover_workflow_files

# ---------- Workspace discovery ----------


@dataclass
class DiscoveryDiagnostics:
    """Discovery SKIP/WARN events; collected for --json, printed to stderr otherwise."""

    warnings: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    _seen_skips: set[tuple[str, str]] = field(default_factory=set)

    def skip(self, path: Path, ecosystem: str, reason: str) -> None:
        """Record a manifest that was found but is not actionable.

        Every manifest on disk maps to exactly one of ok / skip / error; a
        silent drop here would make a coverage gap indistinguishable from
        "nothing to upgrade" (see #24). Deduped by (path, ecosystem) so a
        manifest reachable through two overlapping repo walks (e.g. a nested
        git repo) is not double-counted in ``discovery skipped``.
        """
        key = (str(path), ecosystem)
        if key in self._seen_skips:
            return
        self._seen_skips.add(key)
        self.skipped.append(
            {"path": str(path), "ecosystem": ecosystem, "reason": reason},
        )

    def skip_node(self, path: Path, reason: str) -> None:
        self.skip(path, "node", reason)

    def warn(self, path: Path, message: str) -> None:
        self.warnings.append({"path": str(path), "message": message})

    def warn_root_not_found(self, root: Path) -> None:
        self.warnings.append({"path": str(root), "message": "root not found"})


def discover_workspaces_in_root(
    root: Path,
    flat: bool,
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
    follow_symlinks: bool = False,
) -> list[Workspace]:
    found: list[Workspace] = []
    # Workspace roots whose members are governed by a single root lockfile and
    # must not be enumerated as standalone workspaces (npm/yarn ``workspaces``
    # field; uv ``[tool.uv.workspace].members``). Populated as the root dir is
    # visited, then consulted to suppress its descendants. The recursive walk is
    # top-down, so a root is always seen before its members.
    node_workspace_roots: dict[Path, set[str]] = {}
    uv_roots: dict[Path, set[str]] = {}

    def record_skip(mdir: Path, ecosystem: str, reason: str) -> None:
        """A manifest was found but is not actionable — count it, never drop it.

        Silent omission (the core defect in #24) makes a coverage gap read
        identically to "nothing to upgrade".
        """
        if diagnostics is not None:
            diagnostics.skip(mdir, ecosystem, reason)
        else:
            print(f"SKIP {ecosystem} {mdir}: {reason}", file=sys.stderr)

    def consider_node(mdir: Path) -> None:
        if not (mdir / "package.json").exists():
            return
        mgr, warn = detect_node_manager(mdir)
        if not mgr:
            record_skip(mdir, "node", warn or "unrecognized package manager")
            return
        if warn:
            if diagnostics is not None:
                diagnostics.warn(mdir, warn)
            else:
                print(f"WARN {mdir}: {warn}", file=sys.stderr)
        for wr, members in node_workspace_roots.items():
            if mdir != wr and path_is_yarn_workspace_child(mdir, wr, members):
                return
        members = yarn_workspace_members(mdir)
        if members:
            node_workspace_roots[mdir] = members
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "node", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="node",
                manager=mgr,
                git_repo=git.resolve(),
                label="",
            )
        )

    def consider_go(mdir: Path) -> None:
        if not (mdir / "go.mod").exists():
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "go", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="go",
                manager="go",
                git_repo=git.resolve(),
                label="",
            )
        )

    def consider_rust(mdir: Path) -> None:
        cargo = mdir / "Cargo.toml"
        if not cargo.exists():
            return
        lock = mdir / "Cargo.lock"
        d = read_toml_file(cargo) or {}
        if not lock.exists() and not d.get("workspace") and not d.get("package"):
            record_skip(
                mdir,
                "rust",
                "Cargo.toml without Cargo.lock and not a [workspace]/[package]",
            )
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "rust", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="rust",
                manager="rust",
                git_repo=git.resolve(),
                label="",
            )
        )

    def consider_py(mdir: Path) -> None:
        pyp = mdir / "pyproject.toml"
        if not (
            pyp.exists()
            or (mdir / "requirements.in").exists()
            or (mdir / "requirements.txt").exists()
            or (mdir / "Pipfile").exists()
        ):
            return
        for ur, members in uv_roots.items():
            if mdir != ur and path_is_uv_workspace_child(mdir, ur, members):
                return
        mgr: str | None = None
        if (mdir / "uv.lock").exists():
            mgr = "uv"
        elif (mdir / "poetry.lock").exists():
            mgr = "poetry"
        elif (mdir / "requirements.in").exists() or (
            mdir / "requirements.txt"
        ).exists():
            mgr = "pip-tools"
        elif pyp.exists():
            d = read_toml_file(pyp) or {}
            build = (d.get("build-system") or {}).get("build-backend", "")
            if "hatch" in build or (d.get("tool") or {}).get("hatch"):
                mgr = "hatch"
        if mgr == "uv":
            members = uv_workspace_members(mdir)
            if members:
                uv_roots[mdir] = members
        if not mgr:
            record_skip(
                mdir,
                "py",
                "pyproject.toml without a recognized manager "
                "(uv/poetry/pip-tools/hatch)",
            )
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "py", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="py",
                manager=mgr,
                git_repo=git.resolve(),
                label="",
            )
        )

    seen_paths: set[Path] = set()
    if flat:
        dir_iter: list[tuple[Path, set[str]]] = list(iter_scanned_dirs(root))
    else:
        # Depth-agnostic, full per-repo discovery: find every git repo under
        # ``root`` using the same recursive walk that drives Actions discovery
        # (``discover_repos``), then recursively scan each repo so that *every*
        # manifest in the tree is enumerated — multiple ecosystems and sub-apps
        # within one repo (e.g. a Go module at the root plus a node app in
        # ``web/``), not just one workspace per repo (#24). Walking each repo
        # (rather than ``root`` wholesale) keeps the scan scoped to real repos
        # and lets per-repo workspace-member suppression run in tree order.
        dir_iter = []
        repos = discover_repos([str(root)], follow_symlinks=follow_symlinks)
        for repo_s in sorted(set(repos.values())):
            dir_iter.extend(iter_scanned_dirs(Path(repo_s)))
        if not dir_iter:
            dir_iter = list(iter_scanned_dirs(root))

    for mdir, names in dir_iter:
        key = mdir.resolve()
        if key in seen_paths:
            continue
        before = len(found)
        if "package.json" in names:
            consider_node(mdir)
        if "go.mod" in names:
            consider_go(mdir)
        if "Cargo.toml" in names:
            consider_rust(mdir)
        if names & {"pyproject.toml", "requirements.in", "requirements.txt", "Pipfile"}:
            consider_py(mdir)
        if len(found) > before:
            seen_paths.add(key)

    return found


def discover_all(
    roots: list[str],
    flat: bool,
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
    follow_symlinks: bool = False,
) -> list[Workspace]:
    all_ws: list[Workspace] = []
    seen: set[tuple[str, str]] = set()
    for r in roots:
        root = Path(r).expanduser().resolve()
        if not root.is_dir():
            if diagnostics is not None:
                diagnostics.warn_root_not_found(root)
            else:
                print(f"WARN: root not found: {root}", file=sys.stderr)
            continue
        for ws in discover_workspaces_in_root(
            root,
            flat=flat and len(roots) == 1,
            diagnostics=diagnostics,
            follow_symlinks=follow_symlinks,
        ):
            key = (str(ws.path), ws.manager)
            if key not in seen:
                seen.add(key)
                all_ws.append(ws)
    return all_ws


def group_by_repo(
    workspaces: list[Workspace], roots: list[str]
) -> dict[Path, RepoBundle]:
    bundles: dict[Path, RepoBundle] = {}
    for ws in workspaces:
        if ws.git_repo not in bundles:
            bundles[ws.git_repo] = RepoBundle(
                git_repo=ws.git_repo,
                label=repo_label(ws.git_repo, roots),
            )
        bundles[ws.git_repo].workspaces.append(WorkspaceResult(workspace=ws))
    return bundles


def merge_action_only_bundles(
    bundles: dict[Path, RepoBundle],
    roots: list[str],
    *,
    follow_symlinks: bool = False,
) -> dict[Path, RepoBundle]:
    """Add git repos that have workflows but no dependency workspaces."""
    for repo_s in discover_repos(roots, follow_symlinks=follow_symlinks).values():
        git_repo = Path(repo_s).resolve()
        if git_repo in bundles:
            continue
        if not discover_workflow_files(git_repo):
            continue
        bundles[git_repo] = RepoBundle(
            git_repo=git_repo,
            label=repo_label(git_repo, roots),
        )
    return bundles
