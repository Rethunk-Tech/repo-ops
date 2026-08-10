"""SBOM (Software Bill of Materials) generation from lockfile dependencies.

Supports CycloneDX 1.4 JSON and SPDX 2.3 JSON output formats.
Reuses the existing lockfile parsers (parsers.py) to enumerate resolved
name@version pairs, then emits a standards-valid SBOM document.

Inline parsers supplement the shared set for lockfile types not yet exposed
in parsers.py (uv.lock/poetry.lock, go.sum, Cargo.lock, requirements.txt).

Also includes container image pins (Dockerfile ``FROM`` / compose
``image:``, emitted as ``pkg:oci`` purls) and GitHub Actions ``uses:`` pins
under ``.github/workflows`` (emitted as ``pkg:github`` purls), reusing the
repo-ops-container and repo-ops-upgrade parsers when they are importable;
both are best-effort and degrade to "no components of that kind" rather
than failing the scan when a parser is unavailable.

Per-component license metadata is emitted as SPDX ``NOASSERTION`` /
CycloneDX ``NOASSERTION`` expression (lockfile parsers do not carry
license ids; use ``repo-ops license`` for policy scanning).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_ops_core.cli import (
    add_roots_argument,
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.filesystem import read_toml_file, should_exclude, write_json_atomic

from repo_ops_lockfiles.license_enrich import enrich_component
from repo_ops_lockfiles.parsers import (
    versions_bun_lock,
    versions_deno_lock,
    versions_npm_lock,
    versions_pdm_lock,
    versions_pipenv_lock,
    versions_pnpm_lock,
    versions_yarn_lock,
)

_log = logging.getLogger(__name__)

# SPDX / CycloneDX policy when lockfiles do not carry license metadata.
_LICENSE_NOASSERTION = "NOASSERTION"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


_REQ_PIN_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*==\s*([^;\s]+)",
)


def _versions_requirements_txt(p: Path) -> set[str]:
    """requirements.txt pinned ``name==ver`` lines → name@version."""
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_PIN_RE.match(line)
        if m:
            out.add(f"{m.group(1)}@{m.group(2)}")
    return out


# ---------------------------------------------------------------------------
# PURL helpers
# ---------------------------------------------------------------------------


def _purl_npm(name: str, version: str) -> str:
    """pkg:npm purl; scoped @scope/pkg handled as-is (widely supported)."""
    return f"pkg:npm/{name}@{version}"


def _purl_jsr(name: str, version: str) -> str:
    """pkg:jsr purl for Deno JSR packages (``@scope/pkg`` names supported)."""
    return f"pkg:jsr/{name}@{version}"


def _purl_pypi(name: str, version: str) -> str:
    """pkg:pypi purl with PEP 503-normalised name."""
    normalized = re.sub(r"[-_.]+", "-", name).lower()
    return f"pkg:pypi/{normalized}@{version}"


def _purl_golang(module: str, version: str) -> str:
    return f"pkg:golang/{module}@{version}"


def _purl_cargo(name: str, version: str) -> str:
    return f"pkg:cargo/{name}@{version}"


def _purl_oci(name: str, ref: str) -> str:
    """pkg:oci purl; *ref* is a digest (``sha256:...``) or a tag."""
    return f"pkg:oci/{name}@{ref}"


def _purl_github(owner_repo: str, ref: str) -> str:
    """pkg:github purl; *owner_repo* is ``owner/repo`` (an Actions action ref)."""
    return f"pkg:github/{owner_repo}@{ref}"


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


def _safe_parse(path: Path, parser: Callable[[Path], Iterable[str]]) -> Iterable[str]:
    """Run *parser* on *path*; log parse failures at debug and yield nothing."""
    try:
        return parser(path)
    except Exception as exc:
        _log.debug("SBOM parse failed for %s: %s", path, exc)
        return ()


def collect_components(
    root: Path,
    *,
    enrich_licenses: bool = False,
    license_cache_dir: Path | None = None,
    allow_network: bool = True,
) -> list[dict[str, str]]:
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
            "ecosystem": ecosystem,
            "license": _LICENSE_NOASSERTION,
        }

    # Container + Actions parsers are optional (best-effort, lazily imported
    # once): a missing/incompatible sibling package degrades to "no
    # components of that kind" rather than failing the whole scan.
    find_container_files: Callable[[Path], list[Path]] | None
    _parse_image_refs: Callable[[Path], list[dict[str, Any]]] | None
    try:
        from repo_ops_container.container import _parse_image_refs as _pir
        from repo_ops_container.container import find_container_files as _fcf

        find_container_files = _fcf
        _parse_image_refs = _pir
    except (ImportError, AttributeError):
        find_container_files = None
        _parse_image_refs = None

    parse_workflow_uses: Callable[[Path], list[Any]] | None
    try:
        from repo_ops_upgrade.workflow_uses import parse_workflow_uses as _pwu

        parse_workflow_uses = _pwu
    except ImportError:
        parse_workflow_uses = None

    seen_container_files: set[Path] = set()

    for dirpath_str, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        dp = Path(dirpath_str)
        if should_exclude(str(dp)):
            dirnames.clear()
            continue
        fname_set = set(filenames)

        # npm lockfiles (one parser per filename; bun.lockb via same text parser)
        _npm_parsers: list[tuple[str, Callable[[Path], set[str]]]] = [
            ("package-lock.json", versions_npm_lock),
            ("yarn.lock", versions_yarn_lock),
            ("pnpm-lock.yaml", versions_pnpm_lock),
            ("bun.lock", versions_bun_lock),
            ("bun.lockb", versions_bun_lock),
        ]
        for lf_name, parser in _npm_parsers:
            if lf_name not in fname_set:
                continue
            for entry in _safe_parse(dp / lf_name, parser):
                if "@" in entry:
                    nm, ver = _split_nv(entry)
                    _add(nm, ver, _purl_npm(nm, ver), "npm")

        # Python lockfiles
        _py_parsers: list[tuple[str, Callable[[Path], set[str]]]] = [
            ("Pipfile.lock", versions_pipenv_lock),
            ("pdm.lock", versions_pdm_lock),
            ("requirements.txt", _versions_requirements_txt),
        ]
        for lf_name, py_parser in _py_parsers:
            if lf_name not in fname_set:
                continue
            for entry in _safe_parse(dp / lf_name, py_parser):
                if "@" in entry:
                    nm, ver = _split_nv(entry)
                    _add(nm, ver, _purl_pypi(nm, ver), "pypi")

        # uv.lock / poetry.lock  (inline parser — TOML [[package]] blocks)
        for lf_name in ("uv.lock", "poetry.lock"):
            if lf_name not in fname_set:
                continue
            for entry in _safe_parse(dp / lf_name, _versions_poetry_uv_lock):
                if "@" in entry:
                    nm, ver = _split_nv(entry)
                    _add(nm, ver, _purl_pypi(nm, ver), "pypi")

        # Go
        if "go.sum" in fname_set:
            for entry in _safe_parse(dp / "go.sum", _versions_go_sum):
                if "@" in entry:
                    nm, ver = _split_nv(entry)
                    _add(nm, ver, _purl_golang(nm, ver), "golang")

        # Rust
        if "Cargo.lock" in fname_set:
            for entry in _safe_parse(dp / "Cargo.lock", _versions_cargo_lock):
                if "@" in entry:
                    nm, ver = _split_nv(entry)
                    _add(nm, ver, _purl_cargo(nm, ver), "cargo")

        # Deno — npm: and jsr: specifiers (http(s): skipped; no stable purl)
        if "deno.lock" in fname_set:
            for entry in _safe_parse(dp / "deno.lock", versions_deno_lock):
                if entry.startswith("npm:"):
                    raw = entry[4:]
                    if "@" in raw:
                        nm, ver = _split_nv(raw)
                        _add(nm, ver, _purl_npm(nm, ver), "npm")
                elif entry.startswith("jsr:"):
                    raw = entry[4:]
                    if "@" in raw:
                        nm, ver = _split_nv(raw)
                        _add(nm, ver, _purl_jsr(nm, ver), "jsr")

        # Container image pins (Dockerfile FROM / compose image:)
        if find_container_files is not None and _parse_image_refs is not None:
            for fpath in find_container_files(dp):
                rp = fpath.resolve()
                if rp in seen_container_files:
                    continue
                seen_container_files.add(rp)
                try:
                    refs = _parse_image_refs(fpath)
                except Exception as exc:
                    _log.debug("SBOM container parse failed for %s: %s", fpath, exc)
                    continue
                for ref in refs:
                    if ref.get("is_stage_ref"):
                        continue
                    image = ref.get("image")
                    pin = ref.get("sha") or ref.get("tag")
                    if not image or not pin:
                        continue
                    _add(image, pin, _purl_oci(image, pin), "oci")

        # GitHub Actions `uses:` pins under .github/workflows
        if (
            parse_workflow_uses is not None
            and dp.name == "workflows"
            and dp.parent.name == ".github"
        ):
            for wf_name in sorted(fname_set):
                if not wf_name.endswith((".yml", ".yaml")):
                    continue
                wf_path = dp / wf_name
                try:
                    pins = parse_workflow_uses(wf_path)
                except Exception as exc:
                    _log.debug("SBOM workflow parse failed for %s: %s", wf_path, exc)
                    continue
                for pin in pins:
                    action = pin.action
                    # Only marketplace/repo action refs (owner/repo) are
                    # packages; local (./path) and reusable-workflow refs
                    # with extra path segments are not.
                    if action.startswith(".") or action.count("/") != 1:
                        continue
                    _add(
                        action,
                        pin.pin,
                        _purl_github(action, pin.pin),
                        "github-actions",
                    )

    components = sorted(seen.values(), key=lambda c: (c["name"], c["version"]))
    if enrich_licenses:
        for component in components:
            license_id = enrich_component(
                component,
                root,
                cache_dir=license_cache_dir,
                allow_network=allow_network,
            )
            if license_id:
                component["license"] = license_id
    return components


# ---------------------------------------------------------------------------
# SBOM document emitters
# ---------------------------------------------------------------------------


def _resolve_tool_version() -> str:
    try:
        return importlib.metadata.version("repo-ops")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _component_license(c: dict[str, str]) -> str:
    lic = (c.get("license") or "").strip()
    return lic or _LICENSE_NOASSERTION


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
                # Lockfile parsers do not carry license ids; explicit policy.
                "licenses": [{"expression": _component_license(c)}],
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
        lic = _component_license(c)
        packages.append(
            {
                "SPDXID": sid,
                "name": c["name"],
                "versionInfo": c["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": lic,
                "licenseDeclared": lic,
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
  Walks lockfiles under root_dir (npm including bun.lock/bun.lockb, Python
  including uv/poetry/pdm/Pipfile/requirements.txt, Go, Rust, Deno).
  Deno emits both npm: (pkg:npm) and jsr: (pkg:jsr) specifiers; http(s):
  remote imports are omitted. De-duplicates by purl across multiple
  lockfiles in the same tree. Go and Rust packages are included only when
  go.sum / Cargo.lock is present. Also includes container image pins
  (Dockerfile FROM / compose image:, as pkg:oci purls) and GitHub Actions
  `uses:` pins under .github/workflows (as pkg:github purls); both
  best-effort, included when their local parsers are importable.
  Component licenses are emitted as NOASSERTION by default. Use
  --enrich-licenses to read local package metadata and optionally query
  registry metadata cached under XDG_CACHE_HOME.
  --enrich-licenses-local reads local metadata and cached registry metadata
  without network access.
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
            "Root directory to scan for lockfiles. When omitted, uses DEFAULT_ROOTS."
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
    parser.add_argument(
        "--enrich-licenses",
        action="store_true",
        default=_env_flag("REPO_OPS_ENRICH_LICENSES"),
        help=(
            "Enrich npm/PyPI/crates licenses from local metadata and cached "
            "registry metadata (opt-in)."
        ),
    )
    parser.add_argument(
        "--enrich-licenses-local",
        action="store_true",
        default=_env_flag("REPO_OPS_ENRICH_LICENSES_LOCAL"),
        help=(
            "Enrich licenses from local metadata and cached registry responses "
            "without network access."
        ),
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
        for comp in collect_components(
            Path(root_str),
            enrich_licenses=args.enrich_licenses or args.enrich_licenses_local,
            allow_network=not args.enrich_licenses_local,
        ):
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

    output_path: str | None = getattr(args, "output", None)
    if output_path:
        write_json_atomic(Path(output_path), doc, compact=args.compact)
    else:
        indent = None if args.compact else 2
        print(json.dumps(doc, indent=indent))

    return 0


if __name__ == "__main__":
    sys.exit(main())
