"""Lockfile version parsers (Node, Python, Deno)."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from repo_ops_core.filesystem import read_json_file, read_toml_file
from repo_ops_core.process import run


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
    def walk(node: dict[str, object]) -> None:
        for name, meta in node.items():
            if not isinstance(meta, dict):
                continue
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
        if not header or not version or header == "__metadata":
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
    """pnpm-lock.yaml → set of name@version.

    Handles both notations:
    - v6+ ``@``-notation: ``foo@1.2.3:`` / ``@scope/bar@2.3.4:``
    - v5/older path-notation: ``/lodash/4.17.21:`` / ``/@babel/core/7.22.0:``
    """
    # v6+ @-notation: name@version key (name may be scoped, e.g. @babel/core@7.x)
    _RE_AT = re.compile(r"^  ['\"]?/?([@\w][^@:'\"]*@[^:'\"\s]+)['\"]?:\s*$")
    # v5 path-notation: /name/version: or /@scope/name/version:
    _RE_PATH = re.compile(
        r"^  ['\"]?/((?:@[^/'\"@]+/)?[^/'\"@]+)/([^/:'\"\s]+)['\"]?:\s*$"
    )
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
            m = _RE_PATH.match(line)
            if m:
                out.add(f"{m.group(1)}@{m.group(2)}")
                continue
            m = _RE_AT.match(line)
            if m:
                token = m.group(1).split("(", 1)[0]
                out.add(token)
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
        if ("@" in key and not key.startswith("@")) or (
            key.startswith("@") and key.count("@") >= 2
        ):
            out.add(key)
        elif (
            isinstance(val, list) and val and isinstance(val[0], str) and "@" in val[0]
        ):
            out.add(val[0])
    return out


def versions_bun_lockb(path_or_mdir: Path) -> set[str]:
    """Legacy binary ``bun.lockb`` → set of name@version.

    Bun prints a yarn.lock-compatible text representation when invoked with a
    readable binary lockfile.  Missing bun, dump failures, and invalid output
    return an empty set.
    """
    path = Path(path_or_mdir)
    lockb = path if path.name == "bun.lockb" else path / "bun.lockb"
    try:
        proc = run(["bun", str(lockb)], cwd=lockb.parent, timeout=60)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return set()
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        return set()
    if text.lower().startswith("error") or "InvalidLockfile" in (proc.stderr or ""):
        return set()

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".lock",
            delete=False,
        ) as handle:
            handle.write(proc.stdout)
            tmp_path = Path(handle.name)
        return versions_yarn_lock(tmp_path)
    except Exception:
        return set()
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def versions_pdm_lock(p: Path) -> set[str]:
    """pdm.lock (TOML) → set of name@version.

    PDM lock files are TOML with a list of ``[[package]]`` entries, each
    containing ``name`` and ``version`` keys.
    """
    d = read_toml_file(p) or {}
    out: set[str] = set()
    for pkg in d.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        ver = pkg.get("version")
        if name and ver:
            out.add(f"{name}@{ver}")
    return out


def versions_poetry_lock(p: Path) -> set[str]:
    """poetry.lock (TOML) → set of name@version.

    Poetry lock files are TOML with a list of ``[[package]]`` entries, each
    containing ``name`` and ``version`` keys.
    """
    d = read_toml_file(p) or {}
    out: set[str] = set()
    for pkg in d.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        ver = pkg.get("version")
        if name and ver:
            out.add(f"{name}@{ver}")
    return out


def versions_uv_lock(p: Path) -> set[str]:
    """uv.lock (TOML) → set of name@version.

    uv lock files are TOML with a list of ``[[package]]`` entries, each
    containing ``name`` (PEP 503-normalized: lowercase, hyphens) and the
    resolved ``version``.
    """
    d = read_toml_file(p) or {}
    out: set[str] = set()
    for pkg in d.get("package", []):
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        ver = pkg.get("version")
        if name and ver:
            out.add(f"{name}@{ver}")
    return out


def versions_pipenv_lock(p: Path) -> set[str]:
    """Pipfile.lock (JSON) → set of name@version.

    Pipfile.lock has ``"default"`` and ``"develop"`` sections; each maps
    package name to a dict with a ``"version"`` key like ``"==2.28.0"``.
    """
    d = read_json_file(p) or {}
    out: set[str] = set()
    for section in ("default", "develop"):
        for name, meta in (d.get(section) or {}).items():
            if not isinstance(meta, dict):
                continue
            ver = meta.get("version", "?")
            # Strip leading "==" or "=" from pinned version specifiers
            ver = ver.lstrip("=")
            out.add(f"{name}@{ver}")
    return out


def versions_deno_lock(p: Path) -> set[str]:
    """deno.lock (JSON) → set of resolved package specifiers.

    Handles all known lockfile versions:

    - v2: top-level ``npm`` dict keyed by ``name@version``.
    - v3: ``packages.specifiers`` (constraint → resolved) plus
      ``packages.npm`` and ``packages.jsr`` sections.
    - v4/v5 (Deno 2.0+): top-level ``specifiers`` (constraint → resolved)
      plus top-level ``npm`` and ``jsr`` sections.
    """
    d = read_json_file(p) or {}
    out: set[str] = set()

    # v4/v5 (Deno 2.0+): specifiers at top level
    for resolved in (d.get("specifiers") or {}).values():
        if isinstance(resolved, str):
            out.add(resolved)

    # v4/v5: jsr section at top level; keys are "@scope/pkg@version"
    for key in d.get("jsr") or {}:
        if isinstance(key, str):
            out.add(f"jsr:{key}")

    # v2 and v4/v5: npm section at top level; keys are "name@version"
    for npm_key in d.get("npm") or {}:
        if isinstance(npm_key, str):
            out.add(f"npm:{npm_key}")

    # v3: packages.specifiers maps import-specifier → resolved specifier
    pkgs = d.get("packages") or {}
    for resolved in (pkgs.get("specifiers") or {}).values():
        if isinstance(resolved, str):
            out.add(resolved)

    # v3: packages.npm section; keys are "name@version"
    for npm_key in pkgs.get("npm") or {}:
        if isinstance(npm_key, str):
            out.add(f"npm:{npm_key}")

    return out
