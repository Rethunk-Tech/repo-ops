"""SBOM (Software Bill of Materials) generation from lockfile dependencies.

Supports CycloneDX 1.4 JSON and SPDX 2.3 JSON output formats.
Reuses the existing lockfile parsers (parsers.py) to enumerate resolved
name@version pairs, then emits a standards-valid SBOM document.

Inline parsers supplement the shared set for lockfile types not yet exposed
in parsers.py (uv.lock/poetry.lock, go.sum, Cargo.lock).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from repo_ops_core.cli import (
    add_roots_argument,
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import read_toml_file

from repo_ops_lockfiles.parsers import (
    versions_bun_lock,
    versions_deno_lock,
    versions_npm_lock,
    versions_pdm_lock,
    versions_pipenv_lock,
    versions_pnpm_lock,
    versions_yarn_lock,
)

# ---------------------------------------------------------------------------
# Inline parsers for lockfile types not exposed in parsers.py
# ---------------------------------------------------------------------------


def _versions_poetry_uv_lock(p: Path) -> set[str]:
    """Parse uv.lock or poetry.lock (TOML top-level [[package]] entries) → name@version.

    Both lockfiles are valid TOML with a top-level ``[[package]]``
    array-of-tables. A prior line-based scanner tracked ``name``/``version``
    across lines and reset only on a literal ``[[package]]`` marker; nested
    ``[[package.dependencies]]`` sub-tables (present in uv.lock) also contain
    bare ``name = "..."`` lines, which overwrote the tracked name before the
    version was flushed and misattributed components. Parsing the TOML
    structure directly and reading only the top-level ``package`` list
    entries avoids this.
    """
    out: set[str] = set()
    data = read_toml_file(p)
    if not data:
        return out
    packages = data.get("package")
    if not isinstance(packages, list):
        return out
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        ver = entry.get("version")
        if isinstance(name, str) and name and isinstance(ver, str) and ver:
            out.add(f"{name}@{ver}")
    return out


def _versions_go_sum(p: Path) -> set[str]:
    """go.sum → module@version pairs (deduped; /go.mod annotation lines excluded)."""
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            mod = parts[0]
            ver_raw = parts[1].split("/")[0]  # strip /go.mod suffix
            out.add(f"{mod}@{ver_raw}")
    return out


def _versions_cargo_lock(p: Path) -> set[str]:
    """Cargo.lock → name@version pairs."""
    out: set[str] = set()
    name: str | None = None
    ver: str | None = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s == "[[package]]":
            if name and ver:
                out.add(f"{name}@{ver}")
            name = ver = None
        elif s.startswith("name = "):
            name = s.split("=", 1)[1].strip().strip('"')
        elif s.startswith("version = "):
            ver = s.split("=", 1)[1].strip().strip('"')
    if name and ver:
        out.add(f"{name}@{ver}")
    return out


# ---------------------------------------------------------------------------
# PURL helpers
# ---------------------------------------------------------------------------


def _purl_npm(name: str, version: str) -> str:
    """pkg:npm purl; scoped @scope/pkg handled as-is (widely supported)."""
    return f"pkg:npm/{name}@{version}"


def _purl_pypi(name: str, version: str) -> str:
    """pkg:pypi purl with PEP 503-normalised name."""
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    return f"pkg:pypi/{normalized}@{version}"


def _purl_golang(module: str, version: str) -> str:
    return f"pkg:golang/{module}@{version}"


def _purl_cargo(name: str, version: str) -> str:
    return f"pkg:cargo/{name}@{version}"


# ---------------------------------------------------------------------------
# Component collection
# ---------------------------------------------------------------------------


def _split_nv(entry: str) -> tuple[str, str]:
    """Split name@version (handles scoped @scope/pkg@ver)."""
    if entry.startswith("@"):
        second_at = entry.find("@", 1)
        if second_at == -1:
            return entry, ""
        return entry[:second_at], entry[second_at + 1 :]
    at = entry.find("@")
    if at == -1:
        return entry, ""
    return entry[:at], entry[at + 1 :]


def collect_components(root: Path) -> list[dict[str, str]]:
    """Walk *root* for lockfiles; return sorted list of unique component dicts.

    Each component has keys: type, name, version, purl, bom-ref.
    De-duplication is by purl.
    """
    seen: dict[str, dict[str, str]] = {}

    def _add(name: str, version: str, purl: str, ecosystem: str) -> None:
        if not name or not version or purl in seen:
            return
        seen[purl] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "bom-ref": f"{ecosystem}:{name}@{version}",
        }

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dp = Path(dirpath_str)
        fname_set = set(filenames)

        # npm lockfiles (one parser per filename)
        _npm_parsers = [
            ("package-lock.json", versions_npm_lock),
            ("yarn.lock", versions_yarn_lock),
            ("pnpm-lock.yaml", versions_pnpm_lock),
            ("bun.lock", versions_bun_lock),
        ]
        for lf_name, parser in _npm_parsers:
            if lf_name not in fname_set:
                continue
            with contextlib.suppress(Exception):
                for entry in parser(dp / lf_name):
                    if "@" in entry:
                        nm, ver = _split_nv(entry)
                        _add(nm, ver, _purl_npm(nm, ver), "npm")

        # Python lockfiles
        _py_parsers = [
            ("Pipfile.lock", versions_pipenv_lock),
            ("pdm.lock", versions_pdm_lock),
        ]
        for lf_name, py_parser in _py_parsers:
            if lf_name not in fname_set:
                continue
            with contextlib.suppress(Exception):
                for entry in py_parser(dp / lf_name):
                    if "@" in entry:
                        nm, ver = _split_nv(entry)
                        _add(nm, ver, _purl_pypi(nm, ver), "pypi")

        # uv.lock / poetry.lock  (inline parser — TOML [[package]] blocks)
        for lf_name in ("uv.lock", "poetry.lock"):
            if lf_name not in fname_set:
                continue
            with contextlib.suppress(Exception):
                for entry in _versions_poetry_uv_lock(dp / lf_name):
                    if "@" in entry:
                        nm, ver = _split_nv(entry)
                        _add(nm, ver, _purl_pypi(nm, ver), "pypi")

        # Go
        if "go.sum" in fname_set:
            with contextlib.suppress(Exception):
                for entry in _versions_go_sum(dp / "go.sum"):
                    if "@" in entry:
                        nm, ver = _split_nv(entry)
                        _add(nm, ver, _purl_golang(nm, ver), "golang")

        # Rust
        if "Cargo.lock" in fname_set:
            with contextlib.suppress(Exception):
                for entry in _versions_cargo_lock(dp / "Cargo.lock"):
                    if "@" in entry:
                        nm, ver = _split_nv(entry)
                        _add(nm, ver, _purl_cargo(nm, ver), "cargo")

        # Deno — emit npm: specifiers only (jsr/http specifiers skipped)
        if "deno.lock" in fname_set:
            with contextlib.suppress(Exception):
                for entry in versions_deno_lock(dp / "deno.lock"):
                    if entry.startswith("npm:"):
                        raw = entry[4:]
                        if "@" in raw:
                            nm, ver = _split_nv(raw)
                            _add(nm, ver, _purl_npm(nm, ver), "npm")

    return sorted(seen.values(), key=lambda c: (c["name"], c["version"]))


# ---------------------------------------------------------------------------
# SBOM document emitters
# ---------------------------------------------------------------------------


def _resolve_tool_version() -> str:
    try:
        return importlib.metadata.version("repo-ops")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def emit_cyclonedx(
    components: list[dict[str, str]],
    root_name: str,
    *,
    tool_version: str | None = None,
    timestamp: str | None = None,
    serial: str | None = None,
) -> dict[str, object]:
    """Return a CycloneDX 1.4 BOM dict suitable for JSON serialisation."""
    tv = tool_version or _resolve_tool_version()
    ts = timestamp or datetime.now(tz=timezone.utc).isoformat()
    sn = serial or str(uuid.uuid4())
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": f"urn:uuid:{sn}",
        "version": 1,
        "metadata": {
            "timestamp": ts,
            "tools": [
                {
                    "vendor": "Rethunk-Tech",
                    "name": "repo-ops",
                    "version": tv,
                }
            ],
            "component": {
                "type": "application",
                "name": root_name,
                "version": "NOASSERTION",
            },
        },
        "components": [
            {
                "type": c["type"],
                "name": c["name"],
                "version": c["version"],
                "purl": c["purl"],
                "bom-ref": c["bom-ref"],
            }
            for c in components
        ],
    }


def _spdx_id(name: str, version: str) -> str:
    """Generate a valid SPDXID from name + version (alphanumeric, dash, dot only)."""
    safe = re.sub(r"[^A-Za-z0-9\-.]", "-", f"{name}-{version}")
    return f"SPDXRef-Package-{safe}"


def emit_spdx(
    components: list[dict[str, str]],
    root_name: str,
    *,
    tool_version: str | None = None,
    timestamp: str | None = None,
    serial: str | None = None,
) -> dict[str, object]:
    """Return an SPDX 2.3 document dict suitable for JSON serialisation."""
    tv = tool_version or _resolve_tool_version()
    ts = timestamp or datetime.now(tz=timezone.utc).isoformat()
    sn = serial or str(uuid.uuid4())

    packages: list[dict[str, object]] = []
    relationships: list[dict[str, str]] = []

    for c in components:
        sid = _spdx_id(c["name"], c["version"])
        packages.append(
            {
                "SPDXID": sid,
                "name": c["name"],
                "versionInfo": c["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": c["purl"],
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": sid,
            }
        )

    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "creationInfo": {
            "created": ts,
            "creators": [f"Tool: repo-ops-{tv}"],
        },
        "name": f"{root_name}-sbom",
        "dataLicense": "CC0-1.0",
        "documentNamespace": f"https://repo-ops.local/sbom/{sn}",
        "packages": packages,
        "relationships": relationships,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SBOM_EPILOG = """\
exit codes:
  0  SBOM emitted successfully
  1  argument error or I/O failure

formats:
  cyclonedx  CycloneDX 1.4 JSON (default)  https://cyclonedx.org/specification/overview/
  spdx       SPDX 2.3 JSON                 https://spdx.github.io/spdx-spec/

scope:
  Walks lockfiles under root_dir (npm, Python, Go, Rust, Deno).
  De-duplicates by purl across multiple lockfiles in the same tree.
  Go and Rust packages are included only when go.sum / Cargo.lock is present.
"""


def build_sbom_parser() -> argparse.ArgumentParser:
    """Return the argument parser for 'repo-ops sbom'."""
    parser = argparse.ArgumentParser(
        prog="repo-ops sbom",
        description=(
            "Emit a CycloneDX or SPDX SBOM from lockfile-resolved dependencies "
            "(read-only; no lockfile writes)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_SBOM_EPILOG,
    )
    add_roots_argument(
        parser,
        help_text=(
            "Root directory to scan for lockfiles. "
            "When omitted, uses DEFAULT_ROOTS."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["cyclonedx", "spdx"],
        default="cyclonedx",
        dest="sbom_format",
        metavar="FORMAT",
        help="SBOM output format: cyclonedx (default) or spdx.",
    )
    parser.add_argument(
        "--output",
        default=None,
        dest="output",
        metavar="PATH",
        help="Write SBOM to PATH instead of stdout.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit single-line JSON instead of pretty-printed.",
    )
    add_verbose_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for 'repo-ops sbom'."""
    parser = build_sbom_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)

    roots = resolve_scan_roots(list(args.roots), parser)

    # Collect components from all roots, de-dup by purl
    purl_seen: set[str] = set()
    all_components: list[dict[str, str]] = []
    for root_str in roots:
        for comp in collect_components(Path(root_str)):
            if comp["purl"] not in purl_seen:
                purl_seen.add(comp["purl"])
                all_components.append(comp)

    root_name = Path(roots[0]).name if roots else "unknown"
    tool_version = _resolve_tool_version()
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    serial = str(uuid.uuid4())

    kwargs: dict[str, str] = {
        "tool_version": tool_version,
        "timestamp": timestamp,
        "serial": serial,
    }

    if args.sbom_format == "spdx":
        doc = emit_spdx(all_components, root_name, **kwargs)
    else:
        doc = emit_cyclonedx(all_components, root_name, **kwargs)

    indent = None if args.compact else 2
    text = json.dumps(doc, indent=indent)

    output_path: str | None = getattr(args, "output", None)
    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
