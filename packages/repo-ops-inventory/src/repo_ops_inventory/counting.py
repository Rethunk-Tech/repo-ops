"""Pure parsing and counting helpers for dependency counting.

Extracted from dep_count.py to remove duplication and enable testing.
Uses shared repo_ops_core.constants, filesystem, process.
"""

from __future__ import annotations

import concurrent.futures
import configparser
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import read_toml_file
from repo_ops_core.git import git_repo_for, run_git
from repo_ops_core.process import run
from repo_ops_core.walk import iter_scanned_dirs, manifest_kinds
from repo_ops_lockfiles.parsers import (
    parse_package_json_direct,
    versions_bun_lock,
    versions_deno_lock,
    versions_npm_lock,
    versions_pdm_lock,
    versions_pipenv_lock,
    versions_pnpm_lock,
    versions_yarn_lock,
)


def count_node(mdir: Path) -> tuple[int, set[str], str]:
    direct = parse_package_json_direct(mdir / "package.json")
    versions: set[str] = set()
    lock = "none"
    if (mdir / "package-lock.json").exists():
        versions = versions_npm_lock(mdir / "package-lock.json")
        lock = "npm"
    elif (mdir / "pnpm-lock.yaml").exists():
        versions = versions_pnpm_lock(mdir / "pnpm-lock.yaml")
        lock = "pnpm"
    elif (mdir / "bun.lock").exists():
        versions = versions_bun_lock(mdir / "bun.lock")
        lock = "bun"
    elif (mdir / "yarn.lock").exists():
        versions = versions_yarn_lock(mdir / "yarn.lock")
        lock = "yarn"
    elif (mdir / "bun.lockb").exists():
        lock = "bun(bin)"
    return direct, versions, lock


def count_installed_node(mdir: Path) -> int:
    nm = mdir / "node_modules"
    if not nm.exists():
        return 0
    # Do NOT prune node_modules here — we want to count nested duplicates from
    # version conflicts (e.g. node_modules/a/node_modules/b). SKIP_DIRS contains
    # "node_modules" to prevent count_node's manifest walk from descending; that
    # shared constant must not be modified. Build a local set that allows descent.
    _skip = SKIP_DIRS - {"node_modules"}
    n = 0
    for dirpath, dirnames, filenames in os.walk(nm):
        dirnames[:] = [d for d in dirnames if d not in _skip]
        if "package.json" in filenames and Path(dirpath) != nm:
            n += 1
    return n


def count_dep_edges(proj: Path) -> int:
    """Sum dependency edges under proj (Node package.json + Python lock/manifests)."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(proj):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        names = set(filenames)
        mdir = Path(dirpath)
        if "package.json" in names:
            total += parse_package_json_direct(mdir / "package.json")
        if "uv.lock" in names:
            total += _count_uv_lock_edges(mdir / "uv.lock")
        elif "poetry.lock" in names:
            total += _count_poetry_lock_edges(mdir / "poetry.lock")
        elif names & {
            "pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "requirements-test.txt",
        }:
            total += _count_python_manifest_edges(mdir, names)
    return total


# ---------- Go ----------


def _go_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return Path(base) / "repo-ops" / "go-cache.json"


def _go_cache_key(mod: Path) -> str:
    """Return a string cache key encoding mod path + mtime_ns of go.mod + go.sum."""
    m_ns = mod.stat().st_mtime_ns
    gosum = mod.parent / "go.sum"
    s_ns = gosum.stat().st_mtime_ns if gosum.exists() else None
    return f"{mod.resolve()}|{m_ns}|{s_ns}"


def load_go_cache(cache_path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(cache_path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_go_cache(cache_path: Path, cache: dict) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=2))
        os.replace(tmp, cache_path)
    except Exception:  # noqa: S110 — best-effort: malformed-cache write silently skipped
        pass


def _count_go_subprocess(mdir: Path) -> tuple[int, set[str]]:
    """Run the actual Go subprocess logic. No caching here."""
    direct = 0
    mod = mdir / "go.mod"
    if mod.exists():
        in_req = False
        for line in mod.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("require ("):
                in_req = True
                continue
            if in_req:
                if s == ")":
                    in_req = False
                    continue
                if s and not s.startswith("//") and "// indirect" not in s:
                    direct += 1
            elif s.startswith("require ") and "(" not in s and "// indirect" not in s:
                direct += 1
    versions: set[str] = set()
    proc = run(["go", "mod", "graph"], cwd=mdir, timeout=60)
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and "@" in parts[1]:
                versions.add(parts[1])
    proc = run(
        [
            "go",
            "list",
            "-deps",
            "-test",
            "-f",
            "{{if .Module}}{{.Module.Path}}@{{.Module.Version}}{{end}}",
            "./...",
        ],
        cwd=mdir,
        timeout=90,
    )
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line and "@" in line and not line.endswith("@"):
                versions.add(line)
    if not versions:
        gosum = mdir / "go.sum"
        if gosum.exists():
            for line in gosum.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    ver = parts[1].split("/")[0]
                    versions.add(f"{parts[0]}@{ver}")
    return direct, versions


def count_go(mdir: Path, go_cache: dict | None = None) -> tuple[int, set[str]]:
    """Count Go deps, using go_cache dict if provided (mutated in-place on miss)."""
    mod = mdir / "go.mod"
    if go_cache is not None and mod.exists():
        try:
            key = _go_cache_key(mod)
            if key in go_cache:
                entry = go_cache[key]
                return entry["direct"], set(entry["versions"])
        except Exception:  # noqa: S110 — best-effort: malformed-manifest cache read skipped
            pass
        direct, versions = _count_go_subprocess(mdir)
        try:
            key = _go_cache_key(mod)
            go_cache[key] = {"direct": direct, "versions": sorted(versions)}
        except Exception:  # noqa: S110 — best-effort: cache-write failure is non-fatal
            pass
        return direct, versions
    return _count_go_subprocess(mdir)


# ---------- Rust ----------


def count_rust(mdir: Path) -> tuple[int, set[str]]:
    direct = 0
    cargo = mdir / "Cargo.toml"
    if cargo.exists():
        d = read_toml_file(cargo)
        if d:
            for k in ("dependencies", "dev-dependencies", "build-dependencies"):
                direct += len(d.get(k) or {})
    versions: set[str] = set()
    lock = mdir / "Cargo.lock"
    if lock.exists():
        name = ver = None
        for line in lock.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s == "[[package]]":
                if name and ver:
                    versions.add(f"{name}@{ver}")
                name = ver = None
            elif s.startswith("name = "):
                name = s.split("=", 1)[1].strip().strip('"')
            elif s.startswith("version = "):
                ver = s.split("=", 1)[1].strip().strip('"')
        if name and ver:
            versions.add(f"{name}@{ver}")
    return direct, versions


# ---------- Python ----------


def count_python(mdir: Path) -> tuple[int, set[str], str]:
    direct = 0
    src_tags = []
    pyp = mdir / "pyproject.toml"
    if pyp.exists():
        src_tags.append("pyproject")
        d = read_toml_file(pyp)
        if d:
            proj = d.get("project") or {}
            direct += len(proj.get("dependencies") or [])
            for group in (proj.get("optional-dependencies") or {}).values():
                direct += len(group or [])
            for group in (d.get("dependency-groups") or {}).values():
                direct += len(group or [])
            poetry = (d.get("tool") or {}).get("poetry") or {}
            direct += len(poetry.get("dependencies") or {})
            direct += len(poetry.get("dev-dependencies") or {})
            for group in (poetry.get("group") or {}).values():
                direct += len((group or {}).get("dependencies") or {})
    for reqname in (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
    ):
        req = mdir / reqname
        if req.exists():
            src_tags.append(reqname.split(".")[0].replace("requirements", "req"))
            direct += sum(
                1
                for ln in req.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.strip()
                and not ln.strip().startswith("#")
                and not ln.strip().startswith("-")
            )
    versions: set[str] = set()
    for lockname in ("uv.lock", "poetry.lock"):
        lf = mdir / lockname
        if not lf.exists():
            continue
        src_tags.append(lockname.split(".")[0])
        name = ver = None
        for line in lf.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s == "[[package]]":
                if name and ver:
                    versions.add(f"{name}@{ver}")
                name = ver = None
            elif s.startswith("name ="):
                name = s.split("=", 1)[1].strip().strip('"')
            elif s.startswith("version ="):
                ver = s.split("=", 1)[1].strip().strip('"')
        if name and ver:
            versions.add(f"{name}@{ver}")
        break
    # pdm.lock (only when uv/poetry lock not already found)
    if not versions:
        pdm_lock = mdir / "pdm.lock"
        if pdm_lock.exists():
            src_tags.append("pdm")
            versions = versions_pdm_lock(pdm_lock)
    # Pipfile.lock (only when no other lockfile found)
    if not versions:
        pipfile_lock = mdir / "Pipfile.lock"
        if pipfile_lock.exists():
            src_tags.append("pipenv")
            versions = versions_pipenv_lock(pipfile_lock)
    return direct, versions, "+".join(src_tags) or "none"


_UV_LOCK_DEP = re.compile(r"^\s+\{ name =")


def _count_uv_lock_edges(lock: Path) -> int:
    """Count resolved dependency edges in a uv.lock file."""
    return sum(
        1
        for ln in lock.read_text(encoding="utf-8", errors="replace").splitlines()
        if _UV_LOCK_DEP.match(ln)
    )


def _count_poetry_lock_edges(lock: Path) -> int:
    """Count dependency edges in poetry.lock [package.dependencies] sections."""
    total = 0
    in_deps = False
    for line in lock.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s in ("[package.dependencies]", "[package.dev-dependencies]"):
            in_deps = True
            continue
        if s.startswith("[") and in_deps:
            in_deps = False
            continue
        if in_deps and s and not s.startswith("#") and "=" in s:
            total += 1
    return total


def _count_pyproject_direct_edges(pyp: Path) -> int:
    """Declared direct deps from a single pyproject.toml (mirrors count_python)."""
    direct = 0
    d = read_toml_file(pyp)
    if not d:
        return 0
    proj = d.get("project") or {}
    direct += len(proj.get("dependencies") or [])
    for group in (proj.get("optional-dependencies") or {}).values():
        direct += len(group or [])
    for group in (d.get("dependency-groups") or {}).values():
        direct += len(group or [])
    poetry = (d.get("tool") or {}).get("poetry") or {}
    direct += len(poetry.get("dependencies") or {})
    direct += len(poetry.get("dev-dependencies") or {})
    for group in (poetry.get("group") or {}).values():
        direct += len((group or {}).get("dependencies") or {})
    return direct


def _count_python_manifest_edges(mdir: Path, names: set[str]) -> int:
    """Declared deps when no lockfile is present in this directory."""
    total = 0
    if "pyproject.toml" in names:
        total += _count_pyproject_direct_edges(mdir / "pyproject.toml")
    for reqname in (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
    ):
        if reqname not in names:
            continue
        req = mdir / reqname
        total += sum(
            1
            for ln in req.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
            and not ln.strip().startswith("#")
            and not ln.strip().startswith("-")
        )
    return total


# ---------- Container ----------


def count_container(proj: Path) -> tuple[int, set[str]]:
    """Count container image pins (FROM + compose image:) across *proj*.

    Walks the project tree for Dockerfile* and compose files, parses each one
    using the repo-ops-container parsers, and returns:
      (direct_pin_count, set_of_"image:tag"_strings)

    Stage references (``FROM <alias>``) are excluded.
    Lazy-imports from repo_ops_container so the inventory package need not
    declare it as a hard dependency.
    """
    try:
        from repo_ops_container.container import (
            _parse_image_refs,
            find_container_files,
        )
    except (ImportError, AttributeError):
        # ImportError covers the module/symbol being missing outright.
        # AttributeError covers a transitive failure while importing
        # repo_ops_container.container itself (e.g. a renamed symbol it
        # references internally) -- without it, that would crash this scan
        # instead of degrading to "container ecosystem unavailable".
        return 0, set()

    direct = 0
    versions: set[str] = set()
    seen: set[Path] = set()

    for dirpath, dirnames, _ in os.walk(proj):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        mdir = Path(dirpath)
        for fpath in find_container_files(mdir):
            rp = fpath.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            try:
                refs = _parse_image_refs(fpath)
            except Exception:  # noqa: S112 — best-effort: malformed container file skipped
                continue
            for ref in refs:
                if ref.get("is_stage_ref"):
                    continue
                img = ref.get("image") or ""
                tag = ref.get("tag") or ""
                sha = ref.get("sha") or ""
                if not img or (not tag and not sha):
                    continue
                pin = f"{img}:{tag}" + (f"@{sha}" if sha else "")
                versions.add(pin)
                direct += 1

    return direct, versions


# ---------- GitHub Actions ----------


def count_actions(proj: Path) -> tuple[int, set[str]]:
    """Count GitHub Actions ``uses:`` pins in ``.github/workflows/``.

    Returns:
      (pin_count, set_of_"action@ref"_strings)

    Lazy-imports from repo_ops_upgrade; gracefully returns zeros if unavailable.
    """
    try:
        from repo_ops_upgrade.actions import (
            discover_workflow_files,
        )
        from repo_ops_upgrade.workflow_uses import (
            WorkflowParseError,
            parse_workflow_uses,
        )
    except (ImportError, AttributeError):
        # AttributeError covers a transitive failure while importing
        # repo_ops_upgrade.actions/workflow_uses themselves (e.g. a renamed
        # symbol referenced internally) degrading gracefully instead of
        # crashing this scan.
        return 0, set()

    direct = 0
    versions: set[str] = set()
    for wf in discover_workflow_files(proj):
        try:
            pins = parse_workflow_uses(wf)
        except WorkflowParseError:
            continue
        for p in pins:
            versions.add(f"{p.action}@{p.pin}")
            direct += 1
    return direct, versions


# ---------- Pre-commit ----------


def count_precommit(proj: Path) -> tuple[int, set[str]]:
    """Count pre-commit ``rev:`` pins in ``.pre-commit-config.yaml`` files.

    Walks the project tree (pruning SKIP_DIRS) and returns:
      (rev_count, set_of_"repo_url@rev"_strings)

    Lazy-imports from repo_ops_upgrade.precommit.
    """
    try:
        from repo_ops_upgrade.precommit import (
            _PRECOMMIT_CONFIG_NAMES,
            _parse_precommit_repos,
        )
    except (ImportError, AttributeError):
        # AttributeError covers a transitive failure while importing
        # repo_ops_upgrade.precommit itself (e.g. a renamed private symbol
        # referenced internally) degrading gracefully instead of crashing
        # this scan.
        return 0, set()

    direct = 0
    versions: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(proj):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if fname not in _PRECOMMIT_CONFIG_NAMES:
                continue
            cfg = Path(dirpath) / fname
            try:
                text = cfg.read_text(encoding="utf-8", errors="replace")
                for entry in _parse_precommit_repos(text):
                    versions.add(f"{entry['repo']}@{entry['rev']}")
                    direct += 1
            except Exception:  # noqa: S110 — best-effort: malformed pre-commit config skipped
                pass
    return direct, versions


# ---------- Submodules ----------


def _parse_gitmodules(gitmodules_path: Path) -> list[str]:
    """Return submodule paths declared in a ``.gitmodules`` file."""
    parser = configparser.ConfigParser()
    try:
        parser.read(gitmodules_path, encoding="utf-8")
    except Exception:
        return []
    paths: list[str] = []
    for section in parser.sections():
        if not section.startswith("submodule "):
            continue
        path = (parser.get(section, "path", fallback="") or "").strip()
        if path:
            paths.append(path)
    return paths


def count_submodule(proj: Path) -> tuple[int, set[str]]:
    """Count git submodule gitlink pins declared under *proj*.

    Walks the project tree for ``.gitmodules`` files, parses each entry's
    declared submodule ``path``, and resolves the pinned commit for that path
    via ``git ls-tree HEAD <path>`` run against the git repo that owns the
    ``.gitmodules`` file (its worktree root).

    Returns:
      (pin_count, set_of_"path@sha"_strings)

    Entirely local (no network, no repo_ops_upgrade import — see AGENTS.md
    layering rules). Gracefully returns zeros for entries whose repo root or
    gitlink can't be resolved (uninitialized submodule, unstaged path, or git
    itself unavailable).
    """
    direct = 0
    versions: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(proj):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if ".gitmodules" not in filenames:
            continue
        gitmodules_path = Path(dirpath) / ".gitmodules"
        repo_root = git_repo_for(gitmodules_path.parent)
        if repo_root is None:
            continue
        for sub_path in _parse_gitmodules(gitmodules_path):
            try:
                proc = run_git(repo_root, "ls-tree", "HEAD", sub_path, timeout=30)
            except (FileNotFoundError, OSError):
                continue
            if proc.returncode != 0:
                continue
            line = proc.stdout.strip()
            if not line:
                continue
            # Gitlink entries look like: "160000 commit <sha>\t<path>"
            parts = line.split()
            if len(parts) < 3 or parts[0] != "160000":
                continue
            sha = parts[2]
            versions.add(f"{sub_path}@{sha}")
            direct += 1

    return direct, versions


# ---------- Deno ----------


def count_deno(mdir: Path) -> tuple[int, set[str]]:
    """Count Deno dependencies from deno.lock and deno.json/deno.jsonc.

    Returns:
      (direct_dep_count, set_of_resolved_specifiers)

    Direct deps come from the ``imports`` map in ``deno.json`` or ``deno.jsonc``.
    Resolved versions come from ``deno.lock`` (all format versions).
    """
    direct = 0
    versions: set[str] = set()
    # Count direct deps from deno.json / deno.jsonc imports map
    for cfg_name in ("deno.json", "deno.jsonc"):
        cfg = mdir / cfg_name
        if not cfg.exists():
            continue
        try:
            import json as _json
            import re as _re

            text = cfg.read_text(encoding="utf-8", errors="replace")
            # Strip line comments for jsonc
            if cfg_name.endswith(".jsonc"):
                text = _re.sub(r"//[^\n]*", "", text)
            d = _json.loads(text)
            imports = d.get("imports") or {}
            direct += len(imports)
        except Exception:  # noqa: S110 — best-effort: malformed deno.json/deno.jsonc is skipped
            pass
        break
    # Count resolved specifiers from deno.lock
    lock = mdir / "deno.lock"
    if lock.exists():
        versions = versions_deno_lock(lock)
    return direct, versions


# ---------- Walk ----------


def find_manifests(root: Path) -> Iterator[tuple[Path, list[str]]]:
    for mdir, names in iter_scanned_dirs(root):
        kinds = manifest_kinds(names)
        if kinds:
            yield mdir, kinds


def _make_row(
    name: str,
    proj: Path,
    go_tasks: list[tuple[dict, Path, dict | None]],
    go_cache: dict | None,
) -> dict[str, Any]:
    """Build a single project row dict, queuing Go tasks for parallel execution."""
    row: dict[str, Any] = {
        "name": name,
        "kinds": set[str](),
        "direct": 0,
        "versions": set[str](),
        "installed": 0,
        "edges": 0,
        "manifests": 0,
    }
    row["edges"] = count_dep_edges(proj)
    for mdir, kinds in find_manifests(proj):
        row["manifests"] += 1
        if "node" in kinds:
            d, v, lock = count_node(mdir)
            row["direct"] += d
            row["versions"] |= v
            row["installed"] += count_installed_node(mdir)
            row["kinds"].add(f"node({lock})")
        if "go" in kinds:
            go_tasks.append((row, mdir, go_cache))
            row["kinds"].add("go")
        if "rust" in kinds:
            d, v = count_rust(mdir)
            row["direct"] += d
            row["versions"] |= v
            row["kinds"].add("rust")
        if "py" in kinds:
            d, v, src = count_python(mdir)
            row["direct"] += d
            row["versions"] |= v
            row["kinds"].add(f"py({src})")
        if "deno" in kinds:
            d, v = count_deno(mdir)
            row["direct"] += d
            row["versions"] |= v
            row["kinds"].add("deno")

    # --- New-ecosystem pin counts (container / actions / pre-commit / submodule) ---
    # These scan the project root rather than individual manifest dirs.
    d_c, v_c = count_container(proj)
    if d_c or v_c:
        row["direct"] += d_c
        row["versions"] |= v_c
        row["kinds"].add("container")

    d_a, v_a = count_actions(proj)
    if d_a or v_a:
        row["direct"] += d_a
        row["versions"] |= v_a
        row["kinds"].add("actions")

    d_p, v_p = count_precommit(proj)
    if d_p or v_p:
        row["direct"] += d_p
        row["versions"] |= v_p
        row["kinds"].add("precommit")

    d_s, v_s = count_submodule(proj)
    if d_s or v_s:
        row["direct"] += d_s
        row["versions"] |= v_s
        row["kinds"].add("submodule")

    if not row["kinds"]:
        row["kinds"] = {"-"}
    return row


def _flat_project_name(root: Path) -> str:
    """Display name for --flat scans (Path('.').name is empty on Python 3.12+)."""
    resolved = root.resolve()
    name = resolved.name
    if name and not name.startswith("."):
        return name
    parts = resolved.as_posix().rstrip("/").split("/")
    return parts[-1] if parts and parts[-1] else str(resolved)


def scan(
    root: Path,
    flat: bool = False,
    go_cache: dict | None = None,
    jobs: int = 8,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    go_tasks: list[tuple[dict, Path, dict | None]] = []

    if flat:
        if root.is_dir():
            proj = root.resolve()
            row = _make_row(_flat_project_name(root), proj, go_tasks, go_cache)
            rows.append(row)
    else:
        if not root.is_dir():
            return rows
        for org in sorted(root.iterdir()):
            if not org.is_dir() or org.name.startswith("."):
                continue
            for proj in sorted(org.iterdir()):
                if not proj.is_dir() or proj.name.startswith("."):
                    continue
                row = _make_row(f"{org.name}/{proj.name}", proj, go_tasks, go_cache)
                rows.append(row)

    if go_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            future_to_row = {
                executor.submit(count_go, mdir, cache): row
                for row, mdir, cache in go_tasks
            }
            for future in concurrent.futures.as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    d, v = future.result()
                except Exception:
                    d, v = 0, set()
                row["direct"] += d
                row["versions"] |= v

    return rows


def _kind_matches(kinds: set, flt: str) -> bool:
    """Return True if any kind string equals flt or starts with flt+'('."""
    return any(k == flt or k.startswith(flt + "(") for k in kinds)
