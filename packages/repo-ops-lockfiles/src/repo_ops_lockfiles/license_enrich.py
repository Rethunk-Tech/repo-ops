"""Opt-in license enrichment from local metadata and package registries.

The default scanners remain offline and unchanged.  This module is used only
when callers explicitly request enrichment; unknown or ambiguous values are
left unresolved rather than converted into guessed SPDX identifiers.
"""

from __future__ import annotations

import email
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

UNKNOWN_LICENSE = "UNKNOWN"

_ALIASES = {
    "0bsd": "0BSD",
    "agpl30": "AGPL-3.0",
    "agpl30only": "AGPL-3.0-only",
    "agpl30orlater": "AGPL-3.0-or-later",
    "apache20": "Apache-2.0",
    "apachelicense20": "Apache-2.0",
    "artistic20": "Artistic-2.0",
    "bsd2clause": "BSD-2-Clause",
    "bsd3clause": "BSD-3-Clause",
    "bsdclause2": "BSD-2-Clause",
    "bsdclause3": "BSD-3-Clause",
    "ccby30": "CC-BY-3.0",
    "ccby40": "CC-BY-4.0",
    "ccbysa40": "CC-BY-SA-4.0",
    "cc01": "CC0-1.0",
    "cddl10": "CDDL-1.0",
    "cddl11": "CDDL-1.1",
    "epl10": "EPL-1.0",
    "epl20": "EPL-2.0",
    "fair": "Fair",
    "gpl20": "GPL-2.0",
    "gpl20only": "GPL-2.0-only",
    "gpl20orlater": "GPL-2.0-or-later",
    "gpl30": "GPL-3.0",
    "gpl30only": "GPL-3.0-only",
    "gpl30orlater": "GPL-3.0-or-later",
    "isc": "ISC",
    "lgpl21": "LGPL-2.1",
    "lgpl21only": "LGPL-2.1-only",
    "lgpl21orlater": "LGPL-2.1-or-later",
    "lgpl30": "LGPL-3.0",
    "lgpl30only": "LGPL-3.0-only",
    "lgpl30orlater": "LGPL-3.0-or-later",
    "mit": "MIT",
    "mit0": "MIT-0",
    "mitlicense": "MIT",
    "mpl20": "MPL-2.0",
    "ncsa": "NCSA",
    "ofl10": "OFL-1.0",
    "ofl11": "OFL-1.1",
    "openssl": "OpenSSL",
    "postgresql": "PostgreSQL",
    "psf20": "PSF-2.0",
    "pythonsoftwarefoundationlicense": "PSF-2.0",
    "ruby": "Ruby",
    "unlicense": "Unlicense",
    "unicode30": "Unicode-3.0",
    "wtfpl": "WTFPL",
    "zlib": "Zlib",
}

_LICENSE_FILE_NAMES = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "COPYING",
    "COPYING.txt",
)
_EXPRESSION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*|[()]")
_OPERATOR_NAMES = {"AND", "OR", "WITH"}


def _alias_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def normalize_spdx(value: object) -> str | None:
    """Return a canonical SPDX id/expression, or ``None`` if not recognized."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or raw.upper() in {"UNKNOWN", "NOASSERTION", "UNLICENSED"}:
        return None

    direct = _ALIASES.get(_alias_key(raw))
    if direct:
        return direct

    tokens = _EXPRESSION_TOKEN.findall(raw)
    if not tokens:
        return None
    compact = re.sub(r"\s+", "", raw)
    if "".join(tokens) != compact:
        return None

    canonical: list[str] = []
    for token in tokens:
        if token in {"(", ")"}:
            canonical.append(token)
            continue
        if token.upper() in _OPERATOR_NAMES:
            canonical.append(token.upper())
            continue
        identifier = _ALIASES.get(_alias_key(token))
        if identifier is None:
            return None
        canonical.append(identifier)
    return " ".join(canonical).replace("( ", "(").replace(" )", ")")


def classify_license_text(text: str) -> str | None:
    """Classify common license texts without making an ambiguous guess."""
    upper = text.upper()
    signatures = (
        ("Apache-2.0", ("APACHE LICENSE", "VERSION 2.0")),
        ("MPL-2.0", ("MOZILLA PUBLIC LICENSE", "2.0")),
        ("GPL-3.0", ("GNU GENERAL PUBLIC LICENSE", "VERSION 3")),
        ("GPL-2.0", ("GNU GENERAL PUBLIC LICENSE", "VERSION 2")),
        ("LGPL-3.0", ("GNU LESSER GENERAL PUBLIC LICENSE", "VERSION 3")),
        ("LGPL-2.1", ("GNU LESSER GENERAL PUBLIC LICENSE", "VERSION 2.1")),
        (
            "BSD-3-Clause",
            (
                "REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS",
                "ENDORSE OR PROMOTE PRODUCTS DERIVED",
            ),
        ),
        ("BSD-2-Clause", ("REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS",)),
        ("MIT", ("PERMISSION IS HEREBY GRANTED, FREE OF CHARGE",)),
        (
            "ISC",
            ("PERMISSION TO USE, COPY, MODIFY, AND/OR DISTRIBUTE THIS SOFTWARE",),
        ),
        (
            "Unlicense",
            ("THIS IS FREE AND UNENCUMBERED SOFTWARE RELEASED INTO THE PUBLIC DOMAIN",),
        ),
    )
    for identifier, needles in signatures:
        if all(needle in upper for needle in needles):
            return identifier
    return None


def license_from_metadata(text: str) -> str | None:
    """Extract a recognized SPDX value from package metadata text."""
    message = email.message_from_string(text)
    for field in ("License-Expression", "License"):
        value = message.get(field)
        normalized = normalize_spdx(value)
        if normalized:
            return normalized
    for classifier in message.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            normalized = normalize_spdx(classifier.rsplit("::", 1)[-1].strip())
            if normalized:
                return normalized
    return classify_license_text(text)


def default_license_cache_dir() -> Path:
    """Return the XDG cache location used for registry responses."""
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".cache") / "repo-ops" / "licenses"


def _cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _registry_json(
    url: str, cache_dir: Path, *, allow_network: bool
) -> dict[str, Any] | None:
    """Read a cached registry response, fetching it only when enabled."""
    cache_file = _cache_path(cache_dir, url)
    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if isinstance(cached, dict):
            return cached
    except (OSError, json.JSONDecodeError):
        pass
    if not allow_network:
        return None

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "repo-ops license enrichment",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return payload


def _json_license(data: object) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("license")
    if raw is None:
        raw = data.get("licenses")
    if isinstance(raw, list):
        values = [normalize_spdx(item) for item in raw]
        values = [item for item in values if item]
        return " OR ".join(values) if values else None
    if isinstance(raw, dict):
        raw = raw.get("type") or raw.get("id")
    return normalize_spdx(raw)


def _license_file(package_dir: Path, declared: object = None) -> str | None:
    if isinstance(declared, str):
        match = re.search(r"SEE LICENSE IN\s+(.+)$", declared, re.IGNORECASE)
        if match:
            candidate = (package_dir / match.group(1).strip()).resolve()
            try:
                candidate.relative_to(package_dir.resolve())
            except ValueError:
                candidate = package_dir / "__missing_license__"
            if candidate.is_file():
                try:
                    return classify_license_text(
                        candidate.read_text(encoding="utf-8", errors="replace")
                    )
                except OSError:
                    pass
    for name in _LICENSE_FILE_NAMES:
        candidate = package_dir / name
        if candidate.is_file():
            try:
                return classify_license_text(
                    candidate.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                continue
    return None


def _npm_local_license(root: Path, name: str, version: str) -> str | None:
    candidates = [root / "node_modules" / name / "package.json"]
    for package_json in root.rglob("package.json"):
        if package_json not in candidates and "node_modules" in package_json.parts:
            candidates.append(package_json)
    for package_json in candidates:
        try:
            data = json.loads(
                package_json.read_text(encoding="utf-8", errors="replace")
            )
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("name") != name or str(data.get("version") or "") != version:
            continue
        license_id = _json_license(data)
        if license_id:
            return license_id
        return _license_file(package_json.parent, data.get("license"))
    for lockfile in root.rglob("package-lock.json"):
        try:
            data = json.loads(lockfile.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        packages = data.get("packages")
        if not isinstance(packages, dict):
            continue
        for entry in packages.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == name and str(entry.get("version") or "") == version:
                license_id = _json_license(entry)
                if license_id:
                    return license_id
    return None


def _npm_registry_license(
    name: str, version: str, cache_dir: Path, *, allow_network: bool
) -> str | None:
    encoded_name = urllib.parse.quote(name, safe="@/")
    encoded_version = urllib.parse.quote(version, safe="")
    url = f"https://registry.npmjs.org/{encoded_name}/{encoded_version}"
    data = _registry_json(url, cache_dir, allow_network=allow_network)
    return _json_license(data)


def _dist_info_license(root: Path, name: str, version: str) -> str | None:
    normalized_name = re.sub(r"[-_.]+", "-", name).lower()
    for venv_name in (".venv", "venv"):
        venv = root / venv_name
        site_packages = list(venv.glob("lib/python*/site-packages"))
        site_packages.append(venv / "Lib" / "site-packages")
        for site in site_packages:
            if not site.is_dir():
                continue
            for dist_info in site.glob("*.dist-info"):
                metadata = dist_info / "METADATA"
                try:
                    text = metadata.read_text(encoding="utf-8", errors="replace")
                    message = email.message_from_string(text)
                except OSError:
                    continue
                if (
                    re.sub(r"[-_.]+", "-", message.get("Name", "")).lower()
                    != normalized_name
                ):
                    continue
                if version and message.get("Version", "") != version:
                    continue
                license_id = license_from_metadata(text)
                if license_id:
                    return license_id
                return _license_file(dist_info.parent / name, None)
    return None


def _pypi_registry_license(
    name: str, version: str, cache_dir: Path, *, allow_network: bool
) -> str | None:
    normalized_name = re.sub(r"[-_.]+", "-", name).lower()
    url = (
        f"https://pypi.org/pypi/{urllib.parse.quote(normalized_name)}/"
        f"{urllib.parse.quote(version, safe='')}/json"
    )
    data = _registry_json(url, cache_dir, allow_network=allow_network)
    info = data.get("info") if isinstance(data, dict) else None
    return _json_license(info)


def _cargo_local_license(name: str, version: str) -> str | None:
    cargo_home = Path(
        os.environ.get("CARGO_HOME", "").strip() or Path.home() / ".cargo"
    )
    sources = cargo_home / "registry" / "src"
    if not sources.is_dir():
        return None
    for package_dir in sources.glob(f"*/{name}-{version}"):
        cargo_toml = package_dir / "Cargo.toml"
        try:
            import tomllib

            data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        package = data.get("package")
        if not isinstance(package, dict):
            continue
        license_id = normalize_spdx(package.get("license"))
        if license_id:
            return license_id
        license_file = package.get("license-file")
        return _license_file(package_dir, license_file)
    return None


def _cargo_registry_license(
    name: str, version: str, cache_dir: Path, *, allow_network: bool
) -> str | None:
    url = (
        f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}/"
        f"{urllib.parse.quote(version, safe='')}"
    )
    data = _registry_json(url, cache_dir, allow_network=allow_network)
    version_data = data.get("version") if isinstance(data, dict) else None
    return _json_license(version_data)


def enrich_license(
    name: str,
    version: str,
    ecosystem: str,
    root: Path | None = None,
    *,
    cache_dir: Path | None = None,
    allow_network: bool = True,
) -> str | None:
    """Resolve a dependency's license from local metadata or cached registries."""
    cache = cache_dir if cache_dir is not None else default_license_cache_dir()
    ecosystem = ecosystem.lower()
    if ecosystem == "npm":
        if root is not None:
            local = _npm_local_license(root, name, version)
            if local:
                return local
        return _npm_registry_license(name, version, cache, allow_network=allow_network)
    if ecosystem in {"pypi", "python"}:
        if root is not None:
            local = _dist_info_license(root, name, version)
            if local:
                return local
        return _pypi_registry_license(name, version, cache, allow_network=allow_network)
    if ecosystem in {"cargo", "rust"}:
        local = _cargo_local_license(name, version)
        if local:
            return local
        return _cargo_registry_license(
            name, version, cache, allow_network=allow_network
        )
    return None


def enrich_component(
    component: dict[str, str],
    root: Path,
    *,
    cache_dir: Path | None = None,
    allow_network: bool = True,
) -> str | None:
    """Resolve a lockfile component's license; non-registry ecosystems skip."""
    ecosystem = component.get("ecosystem", "")
    if not ecosystem:
        ecosystem = (
            component.get("purl", "").split(":", 2)[1]
            if ":" in component.get("purl", "")
            else ""
        )
    return enrich_license(
        component.get("name", ""),
        component.get("version", ""),
        ecosystem,
        root,
        cache_dir=cache_dir,
        allow_network=allow_network,
    )
