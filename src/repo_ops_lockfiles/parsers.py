"""Lockfile version parsers (Node, Python, Go, Rust, Deno)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from repo_ops_core.filesystem import read_json_file, read_toml_file

_PNPM_LOCK_YAML = YAML(typ="safe")


def split_name_version(entry: str) -> tuple[str, str]:
    """Split a ``name@version`` string into (name, version).

    Handles scoped packages like ``@babel/core@7.22.0``.
    """
    if entry.startswith("@"):
        second_at = entry.find("@", 1)
        if second_at == -1:
            return entry, ""
        return entry[:second_at], entry[second_at + 1 :]
    at = entry.find("@")
    if at == -1:
        return entry, ""
    return entry[:at], entry[at + 1 :]


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
            if header is None and line and not line.startswith(" ") and not line.startswith("#"):
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


def _versions_pnpm_lock_regex_fallback(p: Path) -> set[str]:
    """Line-based fallback for a ``pnpm-lock.yaml`` malformed enough that
    ruamel.yaml refuses to parse it (e.g. a truncated/corrupt file) — best
    effort on the two notations we know about, same as the YAML path.
    """
    _re_at = re.compile(r"^  ['\"]?/?([@\w][^@:'\"]*@[^:'\"\s]+)['\"]?:\s*$")
    _re_path = re.compile(r"^  ['\"]?/((?:@[^/'\"@]+/)?[^/'\"@]+)/([^/:'\"\s]+)['\"]?:\s*$")
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
            m = _re_path.match(line)
            if m:
                out.add(f"{m.group(1)}@{m.group(2)}")
                continue
            m = _re_at.match(line)
            if m:
                out.add(m.group(1).split("(", 1)[0])
    return out


def _pnpm_v5_path_name_version(body: str) -> str | None:
    """Try v5 path-notation ``(@scope/)?name/version`` (no ``@`` in name/scope)."""
    parts = body.split("/")
    if len(parts) == 2:
        name, version = parts
        return None if "@" in name else f"{name}@{version}"
    if len(parts) == 3 and parts[0].startswith("@"):
        scope, name, version = parts
        if "@" in scope[1:] or "@" in name:
            return None
        return f"{scope}/{name}@{version}"
    return None


def _pnpm_key_to_name_version(key: str, *, is_path: bool) -> str | None:
    """Resolve one ``packages`` key to a ``name@version`` token.

    Handles v6+ ``name@version`` keys (scoped or not), v5 ``/name/version``
    path keys, and either notation carrying a trailing ``(peer@version)``
    suffix that must be stripped.
    """
    body = key[1:] if is_path else key
    paren = body.find("(")
    if paren != -1:
        body = body[:paren]
    if is_path:
        via_path = _pnpm_v5_path_name_version(body)
        if via_path is not None:
            return via_path
    if not body:
        return None
    name, version = split_name_version(body)
    return f"{name}@{version}" if version else None


def versions_pnpm_lock(p: Path) -> set[str]:
    """pnpm-lock.yaml → set of name@version.

    Handles both notations:
    - v6+ ``@``-notation: ``foo@1.2.3:`` / ``@scope/bar@2.3.4:``
    - v5/older path-notation: ``/lodash/4.17.21:`` / ``/@babel/core/7.22.0:``
    """
    try:
        data = _PNPM_LOCK_YAML.load(p.read_text(encoding="utf-8", errors="replace"))
    except YAMLError:
        return _versions_pnpm_lock_regex_fallback(p)
    if not isinstance(data, dict):
        return set()
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return set()
    out: set[str] = set()
    for raw_key in packages:
        key = str(raw_key)
        token = _pnpm_key_to_name_version(key, is_path=key.startswith("/"))
        if token:
            out.add(token)
    return out


def _bun_lock_text(p: Path) -> str:
    txt = p.read_text(encoding="utf-8", errors="replace")
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)
    # Whole-line comments only: integrity hashes are base64 and carry "//".
    txt = re.sub(r"^\s*//[^\n]*", "", txt, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", txt)


def bun_lock_missing_optionals(p: Path) -> list[str]:
    """``pkg -> optional`` pairs whose optional dependency has no entry in *p*.

    bun drops an optional dependency it cannot resolve, so a lock written between a
    release and its platform binaries' publish carries the package without them.
    """
    try:
        packages = json.loads(_bun_lock_text(p), strict=False).get("packages") or {}
    except (OSError, ValueError, AttributeError):
        return []
    keys = set(packages)
    missing: list[str] = []
    for key, val in packages.items():
        meta = next((v for v in val if isinstance(v, dict)), {}) if isinstance(val, list) else {}
        for name in meta.get("optionalDependencies") or {}:
            if name not in keys and f"{key}/{name}" not in keys:
                missing.append(f"{key} -> {name}")
    return missing


def versions_bun_lock(p: Path) -> set[str]:
    txt = _bun_lock_text(p)
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
        elif isinstance(val, list) and val and isinstance(val[0], str) and "@" in val[0]:
            out.add(val[0])
    return out


def toml_lock_versions(p: Path) -> dict[str, str]:
    """name → version from a TOML lockfile's ``[[package]]`` entries (uv, pdm, poetry)."""
    out: dict[str, str] = {}
    for pkg in (read_toml_file(p) or {}).get("package", []):
        if isinstance(pkg, dict) and pkg.get("name") and pkg.get("version"):
            out[str(pkg["name"])] = str(pkg["version"])
    return out


def _versions_toml_lock(p: Path) -> set[str]:
    """TOML lockfile → set of name@version."""
    return {f"{name}@{ver}" for name, ver in toml_lock_versions(p).items()}


def versions_pdm_lock(p: Path) -> set[str]:
    """pdm.lock (TOML) → set of name@version."""
    return _versions_toml_lock(p)


def versions_poetry_lock(p: Path) -> set[str]:
    """poetry.lock (TOML) → set of name@version."""
    return _versions_toml_lock(p)


def versions_uv_lock(p: Path) -> set[str]:
    """uv.lock (TOML) → set of name@version (PEP 503-normalized names)."""
    return _versions_toml_lock(p)


_REQ_PIN_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^;\s]+)",
)


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

    # v3: packages.jsr section; keys are "@scope/pkg@version"
    for key in pkgs.get("jsr") or {}:
        if isinstance(key, str):
            out.add(f"jsr:{key}")

    return out
