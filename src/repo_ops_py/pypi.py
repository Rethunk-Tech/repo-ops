"""Shared PyPI JSON API lookup for direct-dependency latest-version checks.

Used by the pip ecosystems that have no lockfile-native "latest" query
(poetry/pipenv/pip-tools all resolve a manifest's direct dependencies
against PyPI the same way).
"""

from __future__ import annotations

import json
from http.client import HTTPSConnection
from urllib.parse import quote

from repo_ops_core.constants import TIMEOUT_CHECK
from repo_ops_core.version_utils import semver_tuple


def _pypi_document(package: str) -> dict | None:
    connection = HTTPSConnection("pypi.org", timeout=TIMEOUT_CHECK)
    try:
        connection.request(
            "GET",
            f"/pypi/{quote(package, safe='')}/json",
            headers={"User-Agent": "repo-ops"},
        )
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            return None
        document = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        connection.close()
    return document if isinstance(document, dict) else None


def _unyanked(files: object) -> bool:
    return not isinstance(files, list) or any(
        isinstance(file, dict) and file.get("yanked") is not True for file in files
    )


def pypi_releases(package: str) -> list[str]:
    """Published, unyanked release versions of *package* (empty when the lookup fails)."""
    document = _pypi_document(package) or {}
    releases = document.get("releases")
    if not isinstance(releases, dict):
        return []
    return [v for v, files in releases.items() if isinstance(v, str) and _unyanked(files)]


def pypi_latest(package: str) -> str | None:
    document = _pypi_document(package)
    if document is None:
        return None
    info = document.get("info")
    if isinstance(info, dict) and isinstance(info.get("version"), str):
        return info["version"]
    releases = document.get("releases")
    if not isinstance(releases, dict):
        return None
    candidates = [
        version
        for version, files in releases.items()
        if isinstance(version, str) and _unyanked(files)
    ]
    return max(candidates, key=semver_tuple, default=None)
