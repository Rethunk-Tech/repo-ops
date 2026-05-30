"""Pure parsing and counting helpers for dependency counting.

Extracted from dep_count.py to remove duplication and enable testing.
Uses shared repo_ops.constants, filesystem, process.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
from pathlib import Path
from typing import Any

from repo_ops.core.constants import SKIP_DIRS
from repo_ops.core.filesystem import read_json_file, read_toml_file
from repo_ops.core.process import run

# ---------- Node ----------


def parse_package_json_direct(p: Path) -> int:
    d = read_json_file(p)
    if not d:
        return 0
    return sum(
        len(d.get(k) or {})
        for k in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        )
    )


def versions_npm_lock(p: Path) -> set[str]:
    """package-lock.json packages map → set of name@version (deduped)."""
    d = read_json_file(p)
    if not d:
        return set()
    out: set[str] = set()
    pkgs = d.get("packages")
    if pkgs:
        for path, meta in pkgs.items():
            if not path:
                continue
            name = meta.get("name") or path.rsplit("node_modules/", 1)[-1]
            ver = meta.get("version") or "?"
            out.add(f"{name}@{ver}")
        return out

    # v1 fallback
    def walk(node):
        for name, meta in node.items():
            out.add(f"{name}@{meta.get('version', '?')}")
            walk(meta.get("dependencies") or {})

    walk(d.get("dependencies") or {})
    return out


def versions_yarn_lock(p: Path) -> set[str]:
    """yarn.lock (classic or berry) → set of name@version."""
    text = p.read_text(encoding="utf-8", errors="replace")
    out: set[str] = set()
    blocks = re.split(r"\n\n+", text)
    for block in blocks:
        lines = block.splitlines()
        header = None
        version = None
        for line in lines:
            if (
                header is None
                and line
                and not line.startswith(" ")
                and not line.startswith("#")
            ):
                header = line.rstrip(":").strip()
            m = re.match(r'^\s+version[:\s]+["\']?([^"\']+)["\']?', line)
            if m:
                version = m.group(1).strip()
                break
        if not header or not version:
            continue
        first = header.split(",")[0].strip().strip('"')
        if first.startswith("@"):
            at = first.find("@", 1)
            name = first[:at] if at != -1 else first
        else:
            name = first.split("@", 1)[0]
        out.add(f"{name}@{version}")
    return out


def versions_pnpm_lock(p: Path) -> set[str]:
    out: set[str] = set()
    in_pkgs = False
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.rstrip() == "packages:":
            in_pkgs = True
            continue
        if in_pkgs:
            if line and not line.startswith(" "):
                in_pkgs = False
                continue
            m = re.match(r"^  ['\"]?/?([@\w][^@:'\"]*@[^:'\"\s]+)['\"]?:\s*$", line)
            if m:
                out.add(m.group(1))
    return out


def versions_bun_lock(p: Path) -> set[str]:
    txt = p.read_text(encoding="utf-8", errors="replace")
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    txt = re.sub(r"(^|[^:])//[^\n]*", lambda m: m.group(1), txt)
    txt = re.sub(r",(\s*[}\]])", r"\1", txt)
    try:
        d = json.loads(txt, strict=False)
    except Exception:
        out: set[str] = set()
        m = re.search(r'"packages"\s*:\s*\{', txt)
        if not m:
            return out
        for m2 in re.finditer(r'\[\s*"([^"]+@[^"]+)"', txt[m.end() :]):
            out.add(m2.group(1))
        return out
    out = set()
    for key, val in (d.get("packages") or {}).items():
        if (
            "@" in key
            and not key.startswith("@")
            or (key.startswith("@") and key.count("@") >= 2)
        ):
            out.add(key)
        elif (
            isinstance(val, list) and val and isinstance(val[0], str) and "@" in val[0]
        ):
            out.add(val[0])
    return out


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
    n = 0
    for dirpath, _dn, filenames in os.walk(nm):
        if "package.json" in filenames and Path(dirpath) != nm:
            n += 1
    return n


def count_dep_edges(proj: Path) -> int:
    """Sum dependency edges under proj (Node package.json + Python lock/manifests)."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(proj):
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in (
                ".git",
                ".next",
                "dist",
                "build",
                ".turbo",
                ".cache",
                "target",
                "venv",
                ".venv",
                ".claude",
            )
        ]
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
    return Path(base) / "dep_count" / "go-cache.json"


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
    except Exception:
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
        except Exception:
            pass
        direct, versions = _count_go_subprocess(mdir)
        try:
            key = _go_cache_key(mod)
            go_cache[key] = {"direct": direct, "versions": sorted(versions)}
        except Exception:
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


# ---------- Walk ----------


def find_manifests(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        names = set(filenames)
        kinds = []
        if "package.json" in names:
            kinds.append("node")
        if "go.mod" in names:
            kinds.append("go")
        if "Cargo.toml" in names:
            kinds.append("rust")
        if names & {"pyproject.toml", "requirements.txt", "Pipfile"}:
            kinds.append("py")
        if kinds:
            yield Path(dirpath), kinds


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
    root: Path, flat: bool = False, go_cache: dict | None = None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    go_tasks: list[tuple[dict, Path, dict | None]] = []

    if flat:
        if root.is_dir():
            proj = root.resolve()
            row = _make_row(_flat_project_name(root), proj, go_tasks, go_cache)
            rows.append(row)
    else:
        for org in sorted(root.iterdir()):
            if not org.is_dir() or org.name.startswith("."):
                continue
            for proj in sorted(org.iterdir()):
                if not proj.is_dir() or proj.name.startswith("."):
                    continue
                row = _make_row(f"{org.name}/{proj.name}", proj, go_tasks, go_cache)
                rows.append(row)

    if go_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
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
