"""Workspace discovery and repo grouping for upgrades."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_core.filesystem import read_json_file, read_toml_file
from repo_ops_core.git import git_repo_for, repo_label
from repo_ops_core.process import run
from repo_ops_core.walk import iter_scanned_dirs
from repo_ops_models import RepoBundle, Workspace, WorkspaceResult


def pm_exec_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "exec"],
        "npm": ["npx", "--yes"],
    }.get(manager, ["npx", "--yes"])


def pm_run_prefix(manager: str) -> list[str]:
    return {
        "yarn": ["yarn", "run"],
        "bun": ["bun", "run"],
        "pnpm": ["pnpm", "run"],
        "npm": ["npm", "run"],
    }.get(manager, ["npm", "run"])


def parse_package_manager(pkg: dict) -> tuple[str, str] | None:
    pm = (pkg.get("packageManager") or "").strip()
    if not pm or "@" not in pm:
        return None
    name, ver = pm.split("@", 1)
    return name.lower(), ver


def lockfile_manager(mdir: Path) -> str | None:
    if (mdir / "package-lock.json").exists():
        return "npm"
    if (mdir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (mdir / "bun.lock").exists() or (mdir / "bun.lockb").exists():
        return "bun"
    if (mdir / "yarn.lock").exists():
        return "yarn"
    return None


def detect_node_manager(mdir: Path) -> tuple[str | None, str | None]:
    pkg_path = mdir / "package.json"
    pkg = read_json_file(pkg_path)
    if not pkg:
        return None, "missing or invalid package.json"
    pm_field = parse_package_manager(pkg)
    lock_mgr = lockfile_manager(mdir)
    if pm_field:
        name, _ver = pm_field
        if lock_mgr and lock_mgr != name:
            return (
                name,
                f"WARN: packageManager={name} but lockfile implies {lock_mgr}; using packageManager",
            )
        return name, None
    if lock_mgr:
        return lock_mgr, None
    return None, "no packageManager field and no lockfile"


def corepack_prepare(manager: str, mdir: Path) -> None:
    run(["corepack", "enable"], mdir, timeout=60)
    pkg = read_json_file(mdir / "package.json") or {}
    pm = (pkg.get("packageManager") or "").strip()
    if pm:
        run(["corepack", "prepare", pm, "--activate"], mdir, timeout=120)


def yarn_workspace_members(mdir: Path) -> set[str]:
    pkg = read_json_file(mdir / "package.json") or {}
    members: set[str] = set()
    ws = pkg.get("workspaces")
    if isinstance(ws, list):
        for entry in ws:
            if isinstance(entry, str):
                members.add(entry.rstrip("/"))
    elif isinstance(ws, dict):
        for entry in ws.get("packages") or []:
            if isinstance(entry, str):
                members.add(entry.rstrip("/"))
    return members


def path_is_yarn_workspace_child(mdir: Path, root: Path, members: set[str]) -> bool:
    try:
        rel = mdir.relative_to(root).as_posix()
    except ValueError:
        return False
    for pattern in members:
        pat = pattern.replace("*", "")
        if rel == pattern.rstrip("/*") or rel.startswith(pat):
            return True
        if pattern.endswith("/*") and rel.startswith(pattern[:-2]):
            return True
    return False


# ---------- Workspace discovery ----------


@dataclass
class DiscoveryDiagnostics:
    """Discovery SKIP/WARN events; collected for --json, printed to stderr otherwise."""

    warnings: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def skip_node(self, path: Path, reason: str) -> None:
        self.skipped.append(
            {"path": str(path), "ecosystem": "node", "reason": reason},
        )

    def warn(self, path: Path, message: str) -> None:
        self.warnings.append({"path": str(path), "message": message})

    def warn_root_not_found(self, root: Path) -> None:
        self.warnings.append({"path": str(root), "message": "root not found"})


def discover_workspaces_in_root(
    root: Path,
    flat: bool,
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
) -> list[Workspace]:
    found: list[Workspace] = []
    yarn_roots: dict[Path, set[str]] = {}

    def consider_node(mdir: Path) -> None:
        if not (mdir / "package.json").exists():
            return
        mgr, warn = detect_node_manager(mdir)
        if not mgr:
            reason = warn or "skipped"
            if diagnostics is not None:
                diagnostics.skip_node(mdir, reason)
            else:
                print(f"SKIP node {mdir}: {reason}", file=sys.stderr)
            return
        if warn:
            if diagnostics is not None:
                diagnostics.warn(mdir, warn)
            else:
                print(f"WARN {mdir}: {warn}", file=sys.stderr)
        for yr, members in yarn_roots.items():
            if mdir != yr and path_is_yarn_workspace_child(mdir, yr, members):
                return
        if mgr == "yarn":
            members = yarn_workspace_members(mdir)
            if members:
                yarn_roots[mdir] = members
        git = git_repo_for(mdir)
        if not git:
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
        if (mdir / "go.mod").exists():
            git = git_repo_for(mdir)
            if git:
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
            return
        git = git_repo_for(mdir)
        if git:
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
        if not mgr:
            return
        git = git_repo_for(mdir)
        if git:
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
    for mdir, names in iter_scanned_dirs(root):
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
        if names & {"pyproject.toml", "requirements.in", "Pipfile"}:
            consider_py(mdir)
        if len(found) > before:
            seen_paths.add(key)

    return found


def discover_all(
    roots: list[str],
    flat: bool,
    *,
    diagnostics: DiscoveryDiagnostics | None = None,
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
