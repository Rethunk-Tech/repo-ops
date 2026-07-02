"""Workspace discovery and repo grouping for upgrades."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_container.container import is_container_workspace
from repo_ops_core.discovery import discover_repos
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.git import git_repo_for, repo_label
from repo_ops_core.walk import iter_scanned_dirs
from repo_ops_go.go import register_go_work_dir
from repo_ops_js.workspaces import (
    detect_node_manager,
    path_is_yarn_workspace_child,
    yarn_workspace_members,
)
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_py_uv.uv import path_is_uv_workspace_child, uv_workspace_members

from repo_ops_upgrade.actions import discover_workflow_files
from repo_ops_upgrade.precommit import is_precommit_workspace

# ---------- Cargo workspace helpers ----------


def cargo_workspace_members(mdir: Path) -> set[str]:
    """Return the ``[workspace].members`` globs declared in *mdir*'s Cargo.toml.

    Empty when *mdir* is not a Cargo workspace root.  Members are governed by
    the root ``Cargo.lock`` and must not be enumerated as standalone
    workspaces.  Mirrors ``uv_workspace_members``.
    """
    d = read_toml_file(mdir / "Cargo.toml") or {}
    ws = d.get("workspace") or {}
    if not isinstance(ws, dict):
        return set()
    members = ws.get("members")
    if not isinstance(members, list):
        return set()
    return {m.rstrip("/") for m in members if isinstance(m, str)}


def path_is_cargo_workspace_child(mdir: Path, root: Path, members: set[str]) -> bool:
    """True when *mdir* is a member of the Cargo workspace rooted at *root*.

    Uses the same glob-prefix matching as ``path_is_uv_workspace_child`` and
    ``path_is_yarn_workspace_child``.  Note: Cargo also supports implicit
    auto-discovery (no ``members`` key), ``exclude``, and ``default-members``;
    those cases are not handled here — that's a known limitation.
    """
    try:
        rel = mdir.relative_to(root).as_posix()
    except ValueError:
        return False
    for pattern in members:
        pat = pattern.replace("*", "")
        if rel == pattern.removesuffix("/*").removesuffix("/") or _under(rel, pat):
            return True
        if pattern.endswith("/*") and _under(rel, pattern[:-2]):
            return True
    return False


def _under(rel: str, prefix: str) -> bool:
    """True when *rel* equals *prefix* or sits under it on a path boundary.

    Separator-aware so ``packages/foo`` does not match the sibling
    ``packages/foobar`` (#59).
    """
    prefix = prefix.rstrip("/")
    return rel == prefix or rel.startswith(prefix + "/")


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
    cargo_roots: dict[Path, set[str]] = {}

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
        # Check workspace membership BEFORE manager detection so that a hoisted
        # yarn/npm workspace member (package.json, no own lockfile, no
        # packageManager field) is suppressed silently instead of being recorded
        # as an unactionable skip. Mirrors the uv_roots check in consider_py.
        # The walk is top-down, so a workspace root is always registered before
        # its members are visited. (closes #49)
        for wr, members in node_workspace_roots.items():
            if mdir != wr and path_is_yarn_workspace_child(mdir, wr, members):
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

    def _find_go_work(mdir: Path, git_root: Path) -> Path | None:
        """Return the directory containing go.work nearest to *mdir*, up to *git_root*.

        Walks from *mdir* toward *git_root* (inclusive) and returns the first
        directory that contains a ``go.work`` file, or ``None`` when none is
        found.  This lets the go.work-version-mismatch error hint name the
        exact directory so the operator can run ``go work use`` there.
        """
        current = mdir.resolve()
        ceiling = git_root.resolve()
        while True:
            if (current / "go.work").exists():
                return current
            if current == ceiling:
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None

    def consider_go(mdir: Path) -> None:
        if not (mdir / "go.mod").exists():
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "go", "not inside a git repo")
            return
        ws_path = mdir.resolve()
        # Detect a go.work file between the module dir and the git root so that
        # classification hints in go.py can name the actionable directory.
        go_work_dir = _find_go_work(mdir, git)
        if go_work_dir is not None:
            register_go_work_dir(ws_path, go_work_dir)
        found.append(
            Workspace(
                path=ws_path,
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
        # Suppress Cargo workspace members before any lockfile/package checks,
        # mirroring the uv_roots / node_workspace_roots pattern. A member dir
        # has a Cargo.toml with [package] but no own Cargo.lock; without this
        # guard every member surfaced as a standalone rust workspace. (closes #52)
        for cr, members in cargo_roots.items():
            if mdir != cr and path_is_cargo_workspace_child(mdir, cr, members):
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
        # Register this dir as a cargo workspace root if it declares [workspace]
        # with members globs so descendants can be suppressed.
        ws_members = cargo_workspace_members(mdir)
        if ws_members:
            cargo_roots[mdir] = ws_members
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

    def consider_container(mdir: Path) -> None:
        if not is_container_workspace(mdir):
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "container", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="container",
                manager="container",
                git_repo=git.resolve(),
                label="",
            )
        )

    def consider_precommit(mdir: Path) -> None:
        if not is_precommit_workspace(mdir):
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "precommit", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="precommit",
                manager="precommit",
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
            or (mdir / "Pipfile.lock").exists()
            or (mdir / "pdm.lock").exists()
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
        elif (mdir / "pdm.lock").exists():
            mgr = "pdm"
        elif (mdir / "requirements.in").exists():
            mgr = "pip-tools"
        elif (mdir / "requirements.txt").exists():
            record_skip(
                mdir,
                "py",
                "requirements.in not found; cannot compute upgrades"
                " (generated requirements.txt only)",
            )
            return
        elif (mdir / "Pipfile.lock").exists():
            mgr = "pipenv"
        elif (mdir / "Pipfile").exists():
            # Pipfile without Pipfile.lock: pipenv not yet initialised.
            record_skip(
                mdir,
                "py",
                "Pipfile/pipenv: Pipfile.lock not found; run 'pipenv lock' first",
            )
            return
        elif pyp.exists():
            d = read_toml_file(pyp) or {}
            build = (d.get("build-system") or {}).get("build-backend", "")
            if "hatch" in build or (d.get("tool") or {}).get("hatch"):
                mgr = "hatch"
            elif (d.get("tool") or {}).get("pdm") is not None:
                # pyproject.toml with [tool.pdm] but no pdm.lock yet.
                record_skip(
                    mdir,
                    "py",
                    "pdm.lock not found; run 'pdm lock' first",
                )
                return
        if mgr == "uv":
            members = uv_workspace_members(mdir)
            if members:
                uv_roots[mdir] = members
        if not mgr:
            record_skip(
                mdir,
                "py",
                "pyproject.toml without a recognized manager "
                "(uv/poetry/pdm/pip-tools/hatch/pipenv)",
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

    def consider_deno(mdir: Path) -> None:
        if not (
            (mdir / "deno.json").exists()
            or (mdir / "deno.jsonc").exists()
            or (mdir / "deno.lock").exists()
        ):
            return
        git = git_repo_for(mdir)
        if not git:
            record_skip(mdir, "deno", "not inside a git repo")
            return
        found.append(
            Workspace(
                path=mdir.resolve(),
                ecosystem="deno",
                manager="deno",
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
        if "package.json" in names:
            consider_node(mdir)
        if "go.mod" in names:
            consider_go(mdir)
        if "Cargo.toml" in names:
            consider_rust(mdir)
        if names & {
            "pyproject.toml",
            "requirements.in",
            "requirements.txt",
            "Pipfile",
            "Pipfile.lock",
            "pdm.lock",
        }:
            consider_py(mdir)
        if names & {"deno.json", "deno.jsonc", "deno.lock"}:
            consider_deno(mdir)
        consider_container(mdir)
        consider_precommit(mdir)
        # Mark this dir visited unconditionally (not only when a workspace was
        # added) so that skip-only dirs are deduped across nested-repo overlap.
        # Without this, a dir that produces only a record_skip is walked a
        # second time by the overlapping repo walk, printing SKIP twice on
        # stderr (diagnostics._seen_skips dedupes --json only). (closes #46)
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
