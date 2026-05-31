"""Node lockfile version parsers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_ops_core.filesystem import read_json_file


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
