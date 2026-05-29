#!/usr/bin/env python3
"""Count dependencies per project (direct + reachable versions + on-disk).

Usage: dep_count.py [root_dir ...]
Default root: /usr/local/src/com.github

Columns:
  direct    Declared in manifest(s) (package.json / go.mod / Cargo.toml / pyproject)
  versions  Distinct name@version pairs reachable across lockfiles + build graph.
            For Go uses `go mod graph` (all reachable versions, NOT just MVS-selected).
            For Node parses yarn/npm/pnpm/bun lockfiles for every resolved version.
            For Rust parses Cargo.lock [[package]] blocks.
            Multiple major-version copies of same crate/lib counted separately.
  on-disk   Actual node_modules/**/package.json instance count (true sprawl,
            includes nested duplicates from version conflicts).
  manifests Number of manifest files found (depth ≥ 1).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore


SKIP_DIRS = {"node_modules", ".git", ".next", "dist", "build", "out",
             ".turbo", ".cache", "vendor", "third_party", ".venv", "venv",
             ".claude", "target", ".yarn", "coverage", ".nuxt", ".svelte-kit",
             ".worktrees"}


# ---------- Node ----------

def parse_package_json_direct(p: Path) -> int:
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return 0
    return sum(len(d.get(k) or {}) for k in
               ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"))


def versions_npm_lock(p: Path) -> set[str]:
    """package-lock.json packages map → set of name@version (deduped)."""
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
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
    # Split into blocks separated by blank lines.
    blocks = re.split(r"\n\n+", text)
    for block in blocks:
        lines = block.splitlines()
        header = None
        version = None
        for line in lines:
            if header is None and line and not line.startswith(" ") and not line.startswith("#"):
                header = line.rstrip(":").strip()
            m = re.match(r'^\s+version[:\s]+["\']?([^"\']+)["\']?', line)
            if m:
                version = m.group(1).strip()
                break
        if not header or not version:
            continue
        first = header.split(",")[0].strip().strip('"')
        # name@range → take name (handles @scope/name@range)
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
    txt = re.sub(r",(\s*[}\]])", r"\1", txt)  # strip trailing commas (JSONC)
    try:
        d = json.loads(txt, strict=False)
    except Exception:
        out: set[str] = set()
        m = re.search(r'"packages"\s*:\s*\{', txt)
        if not m:
            return out
        # bun packages values: ["name@version", ...] — capture first quoted element
        for m2 in re.finditer(r'\[\s*"([^"]+@[^"]+)"', txt[m.end():]):
            out.add(m2.group(1))
        return out
    out = set()
    for key, val in (d.get("packages") or {}).items():
        if "@" in key and not key.startswith("@") or (key.startswith("@") and key.count("@") >= 2):
            out.add(key)
        elif isinstance(val, list) and val and isinstance(val[0], str) and "@" in val[0]:
            out.add(val[0])
    return out


def count_node(mdir: Path) -> tuple[int, set[str], str]:
    direct = parse_package_json_direct(mdir / "package.json")
    versions: set[str] = set()
    lock = "none"
    if (mdir / "package-lock.json").exists():
        versions = versions_npm_lock(mdir / "package-lock.json"); lock = "npm"
    elif (mdir / "pnpm-lock.yaml").exists():
        versions = versions_pnpm_lock(mdir / "pnpm-lock.yaml"); lock = "pnpm"
    elif (mdir / "bun.lock").exists():
        versions = versions_bun_lock(mdir / "bun.lock"); lock = "bun"
    elif (mdir / "yarn.lock").exists():
        versions = versions_yarn_lock(mdir / "yarn.lock"); lock = "yarn"
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
    """Sum declared deps across EVERY package.json under proj (incl. node_modules).

    Each installed package.json declares its own dependencies; this counts every
    declared edge in the installed graph. Heavy double-counting by design.
    """
    total = 0
    for dirpath, dirnames, filenames in os.walk(proj):
        # Don't prune node_modules — that's the point.
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", ".next", "dist", "build", ".turbo",
                                    ".cache", "target", "venv", ".venv",
                                    ".claude")]
        if "package.json" in filenames:
            total += parse_package_json_direct(Path(dirpath) / "package.json")
    return total


# ---------- Go ----------

def _go_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "dep_count" / "go-cache.json"


def _go_cache_key(mod: Path) -> str:
    """Return a string cache key encoding mod path + mtime_ns of go.mod + go.sum."""
    m_ns = mod.stat().st_mtime_ns
    gosum = mod.parent / "go.sum"
    s_ns = gosum.stat().st_mtime_ns if gosum.exists() else None
    return f"{mod.resolve()}|{m_ns}|{s_ns}"


def load_go_cache(cache_path: Path) -> dict:
    try:
        return json.loads(cache_path.read_text(encoding="utf-8", errors="replace"))
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
                in_req = True; continue
            if in_req:
                if s == ")":
                    in_req = False; continue
                if s and not s.startswith("//") and "// indirect" not in s:
                    direct += 1
            elif s.startswith("require ") and "(" not in s and "// indirect" not in s:
                direct += 1
    versions: set[str] = set()
    # `go mod graph`: every required-module edge (incl. test deps of direct mods).
    try:
        out = subprocess.run(["go", "mod", "graph"], cwd=mdir,
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and "@" in parts[1]:
                    versions.add(parts[1])
    except Exception:
        pass
    # `go list -deps -test ./...`: modules actually imported by code + tests.
    try:
        out = subprocess.run(
            ["go", "list", "-deps", "-test",
             "-f", "{{if .Module}}{{.Module.Path}}@{{.Module.Version}}{{end}}",
             "./..."],
            cwd=mdir, capture_output=True, text=True, timeout=90)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                line = line.strip()
                if line and "@" in line and not line.endswith("@"):
                    versions.add(line)
    except Exception:
        pass
    if not versions:
        gosum = mdir / "go.sum"
        if gosum.exists():
            for line in gosum.read_text(encoding="utf-8", errors="replace").splitlines():
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
        try:
            d = tomllib.loads(cargo.read_text(encoding="utf-8", errors="replace"))
            for k in ("dependencies", "dev-dependencies", "build-dependencies"):
                direct += len(d.get(k) or {})
        except Exception:
            pass
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
        try:
            d = tomllib.loads(pyp.read_text(encoding="utf-8", errors="replace"))
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
        except Exception:
            pass
    for reqname in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
        req = mdir / reqname
        if req.exists():
            src_tags.append(reqname.split(".")[0].replace("requirements", "req"))
            direct += sum(1 for l in req.read_text(encoding="utf-8", errors="replace").splitlines()
                          if l.strip() and not l.strip().startswith("#")
                          and not l.strip().startswith("-"))
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


# ---------- Walk ----------

def find_manifests(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        names = set(filenames)
        kinds = []
        if "package.json" in names: kinds.append("node")
        if "go.mod" in names: kinds.append("go")
        if "Cargo.toml" in names: kinds.append("rust")
        if names & {"pyproject.toml", "requirements.txt", "Pipfile"}: kinds.append("py")
        if kinds:
            yield Path(dirpath), kinds


def _make_row(name: str, proj: Path, go_tasks: list, go_cache: dict | None) -> dict:
    """Build a single project row dict, queuing Go tasks for parallel execution."""
    row = {"name": name, "kinds": set(),
           "direct": 0, "versions": set(), "installed": 0,
           "edges": 0, "manifests": 0}
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
            # Defer Go subprocess work for parallel execution below.
            go_tasks.append((row, mdir, go_cache))
            row["kinds"].add("go")
        if "rust" in kinds:
            d, v = count_rust(mdir)
            row["direct"] += d; row["versions"] |= v
            row["kinds"].add("rust")
        if "py" in kinds:
            d, v, src = count_python(mdir)
            row["direct"] += d; row["versions"] |= v
            row["kinds"].add(f"py({src})")
    if not row["kinds"]:
        row["kinds"] = {"-"}
    return row


def scan(root: Path, flat: bool = False, go_cache: dict | None = None) -> list[dict]:
    rows = []
    # go_tasks: list of (row, mdir, go_cache) triples for parallel subprocess execution.
    go_tasks: list[tuple[dict, Path, dict | None]] = []

    if flat:
        # --flat: treat root itself as the single project (one level).
        if root.is_dir() and not root.name.startswith("."):
            row = _make_row(root.name, root, go_tasks, go_cache)
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

    # Run all Go module scans in parallel (subprocess-bound, safe for threads).
    if go_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_row = {executor.submit(count_go, mdir, cache): row
                             for row, mdir, cache in go_tasks}
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


def main():
    parser = argparse.ArgumentParser(
        description="Count dependencies per project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "roots", nargs="*", default=["/usr/local/src/com.github"],
        metavar="root_dir",
        help="Root directories to scan (default: /usr/local/src/com.github)",
    )
    parser.add_argument(
        "--json", dest="json_out", action="store_true",
        help="Emit JSON instead of the human-readable table.",
    )
    parser.add_argument(
        "--sort", choices=["name", "direct", "versions", "on-disk", "edges"],
        default="name",
        help="Sort column: name (asc), others descending. Default: name.",
    )
    parser.add_argument(
        "--filter", dest="filter_kind",
        choices=["node", "go", "rust", "py"],
        metavar="KIND",
        help="Only show rows whose kinds include KIND (node/go/rust/py).",
    )
    parser.add_argument(
        "--flat", action="store_true",
        help="Treat each root as a single project (skip org/proj two-level walk).",
    )
    parser.add_argument(
        "--no-cache", dest="no_cache", action="store_true",
        help="Bypass XDG Go module result cache.",
    )
    args = parser.parse_args()

    # Load Go cache (shared dict mutated in-place during scan).
    cache_path = _go_cache_path()
    go_cache: dict | None = None
    if not args.no_cache:
        go_cache = load_go_cache(cache_path)

    scanned_roots = [str(Path(r).resolve()) for r in args.roots]
    all_rows = []
    for r in args.roots:
        all_rows.extend(scan(Path(r), flat=args.flat, go_cache=go_cache))

    # Persist updated cache.
    if not args.no_cache and go_cache is not None:
        save_go_cache(cache_path, go_cache)

    # Annotate rows.
    for r in all_rows:
        r["kinds_s"] = ",".join(sorted(r["kinds"]))
        r["versions_n"] = len(r["versions"])

    # Filter (after scan, before sort).
    if args.filter_kind:
        all_rows = [r for r in all_rows if _kind_matches(r["kinds"], args.filter_kind)]

    # Sort.
    sort_key = args.sort
    if sort_key == "name":
        all_rows.sort(key=lambda x: x["name"])
    elif sort_key == "direct":
        all_rows.sort(key=lambda x: x["direct"], reverse=True)
    elif sort_key == "versions":
        all_rows.sort(key=lambda x: x["versions_n"], reverse=True)
    elif sort_key == "on-disk":
        all_rows.sort(key=lambda x: x["installed"], reverse=True)
    elif sort_key == "edges":
        all_rows.sort(key=lambda x: x["edges"], reverse=True)

    if args.json_out:
        td = tv = ti = te = 0
        json_rows = []
        for r in all_rows:
            json_rows.append({
                "name": r["name"],
                "kinds": sorted(r["kinds"]),
                "direct": r["direct"],
                "versions": r["versions_n"],
                "on_disk": r["installed"],
                "edges": r["edges"],
            })
            td += r["direct"]; tv += r["versions_n"]
            ti += r["installed"]; te += r["edges"]
        print(json.dumps({
            "rows": json_rows,
            "totals": {"direct": td, "versions": tv, "on_disk": ti, "edges": te},
            "scanned_roots": scanned_roots,
        }, indent=2))
        return

    # Human-readable table.
    name_w = max((len(r["name"]) for r in all_rows), default=40)
    kind_w = min(40, max((len(r["kinds_s"]) for r in all_rows), default=10))
    header = f"{'project'.ljust(name_w)}  {'kinds'.ljust(kind_w)}  mans  direct  versions  on-disk    edges"
    print(header); print("-" * len(header))
    td = tv = ti = te = tm = 0
    for r in all_rows:
        k = r["kinds_s"]
        if len(k) > kind_w: k = k[:kind_w-1] + "…"
        print(f"{r['name'].ljust(name_w)}  {k.ljust(kind_w)}  "
              f"{r['manifests']:>4}  {r['direct']:>6}  {r['versions_n']:>8}  {r['installed']:>7}  {r['edges']:>7}")
        td += r["direct"]; tv += r["versions_n"]; ti += r["installed"]
        te += r["edges"]; tm += r["manifests"]
    print("-" * len(header))
    print(f"{'TOTAL'.ljust(name_w)}  {''.ljust(kind_w)}  {tm:>4}  {td:>6}  {tv:>8}  {ti:>7}  {te:>7}")


if __name__ == "__main__":
    main()
