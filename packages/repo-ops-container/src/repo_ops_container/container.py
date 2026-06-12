"""Container image pin check/apply for Dockerfile* and compose files.

Design notes
------------
* Discovery: a directory is a container workspace when it contains any of
  ``Dockerfile``, ``Dockerfile.<suffix>``, ``*.dockerfile``,
  ``docker-compose.yml``, ``docker-compose.yaml``, ``compose.yml``,
  ``compose.yaml``.
* Check (read-only): parses FROM lines and compose ``image:`` values, queries
  the registry over anonymous HTTP (urllib, stdlib-only), returns Change
  objects.  Network failures and non-semver tags are recorded report-only
  (detail string prefix ``[report-only]``).
* Apply: byte-preserving single-line edits — only the tag portion of the
  matched line changes; never reflows.  SHA-pinned images (``@sha256:...``)
  are report-only by default; set ``REPO_OPS_CONTAINER_UPDATE_LATEST=1`` to
  allow cross-major bumps (still no SHA resolution).
* Verify: v1 has no verify step; building images is out of scope.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from repo_ops_core.version_utils import semver_tuple
from repo_ops_models import Change, Workspace

# ---------------------------------------------------------------------------
# File patterns that signal a container workspace
# ---------------------------------------------------------------------------

_DOCKERFILE_RE = re.compile(r"^(Dockerfile(\..+)?|.+\.dockerfile)$", re.IGNORECASE)
_COMPOSE_NAMES = frozenset(
    {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }
)


def is_container_workspace(directory: Path) -> bool:
    """True when *directory* contains at least one Dockerfile or compose file."""
    try:
        for entry in directory.iterdir():
            if entry.is_file():
                if _DOCKERFILE_RE.match(entry.name):
                    return True
                if entry.name in _COMPOSE_NAMES:
                    return True
    except PermissionError:
        pass
    return False


def find_container_files(directory: Path) -> list[Path]:
    """Return all Dockerfile* and compose files in *directory* (non-recursive)."""
    found: list[Path] = []
    try:
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            if _DOCKERFILE_RE.match(entry.name) or entry.name in _COMPOSE_NAMES:
                found.append(entry)
    except PermissionError:
        pass
    return found


# ---------------------------------------------------------------------------
# FROM line parsing
# ---------------------------------------------------------------------------

# Matches FROM lines in Dockerfiles:
#   FROM [--platform=...] <image>[:<tag>][@sha256:<digest>] [AS <stage>]
# Groups: (platform_opt, image_ref, tag_or_none, sha_or_none, stage_or_none)
_FROM_RE = re.compile(
    r"^FROM\s+"
    r"(?:--platform=\S+\s+)?"  # optional --platform=...
    r"(?P<image>[^\s:@]+)"  # image name (no tag/digest/space)
    r"(?::(?P<tag>[^\s@]+))?"  # optional :tag
    r"(?:@(?P<sha>sha256:[0-9a-fA-F]+))?"  # optional @sha256:...
    r"(?:\s+AS\s+\S+)?"  # optional AS <stage>
    r"\s*$",
    re.IGNORECASE,
)

# Reserved identifiers that are not real image names
_SCRATCH_NAMES = frozenset({"scratch"})


def _parse_from_lines(text: str) -> list[dict[str, Any]]:
    """Parse all FROM lines in a Dockerfile text.

    Returns a list of dicts with keys:
      line_no (int), image (str), tag (str|None), sha (str|None),
      is_stage_ref (bool)

    Stage references (FROM <previously-defined-stage>) are flagged
    ``is_stage_ref=True`` and should be skipped.
    """
    results: list[dict[str, Any]] = []
    defined_stages: set[str] = set()

    for i, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line.upper().startswith("FROM"):
            continue
        m = _FROM_RE.match(line)
        if not m:
            continue
        image = m.group("image")
        tag = m.group("tag")
        sha = m.group("sha")

        # Detect stage alias (AS <name>) and register it
        alias_m = re.search(r"\bAS\s+(\S+)\s*$", line, re.IGNORECASE)
        if alias_m:
            defined_stages.add(alias_m.group(1).lower())

        is_stage_ref = (
            image.lower() in _SCRATCH_NAMES or image.lower() in defined_stages
        )
        results.append(
            {
                "line_no": i,
                "image": image,
                "tag": tag,
                "sha": sha,
                "is_stage_ref": is_stage_ref,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Compose image: value parsing
# ---------------------------------------------------------------------------

# Matches «image: nginx:1.25-alpine» or «image: "nginx:1.25"»
_COMPOSE_IMAGE_RE = re.compile(
    r'^(?P<indent>[ \t]*)image:\s*["\']?'
    r'(?P<image>[^\s:"\']+)'
    r'(?::(?P<tag>[^\s@"\']+))?'
    r"(?:@(?P<sha>sha256:[0-9a-fA-F]+))?"
    r'["\']?\s*$'
)


def _parse_compose_images(text: str) -> list[dict[str, Any]]:
    """Parse ``image:`` lines in a compose file.

    Returns dicts with: line_no, image, tag, sha (same shape as _parse_from_lines).
    is_stage_ref is always False for compose images.
    """
    results: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _COMPOSE_IMAGE_RE.match(line)
        if not m:
            continue
        image = m.group("image")
        tag = m.group("tag")
        sha = m.group("sha")
        results.append(
            {
                "line_no": i,
                "image": image,
                "tag": tag,
                "sha": sha,
                "is_stage_ref": False,
            }
        )
    return results


def _parse_image_refs(path: Path) -> list[dict[str, Any]]:
    """Parse image references from *path* (auto-detects Dockerfile vs compose)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.name.lower()
    if name in _COMPOSE_NAMES or "compose" in name:
        return _parse_compose_images(text)
    return _parse_from_lines(text)


# ---------------------------------------------------------------------------
# Registry HTTP client (stdlib urllib, anonymous only)
# ---------------------------------------------------------------------------

_REGISTRY_TIMEOUT = 10  # seconds


def _docker_hub_token(image_name: str) -> str | None:
    """Fetch an anonymous pull token for docker.io."""
    # image_name may be «library/nginx» or «nginx»
    scope = f"repository:{image_name}:pull"
    url = f"https://auth.docker.io/token?service=registry.docker.io&scope={scope}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # noqa: S310
            data: dict[str, Any] = json.loads(resp.read())
            token = data.get("token") or data.get("access_token")
            return str(token) if token else None
    except Exception:
        return None


def _normalize_docker_image(image: str) -> tuple[str, str]:
    """Return (registry_host, repo_path) for an image reference.

    Examples:
      «nginx»            → («registry-1.docker.io», «library/nginx»)
      «user/app»         → («registry-1.docker.io», «user/app»)
      «ghcr.io/org/img»  → («ghcr.io», «org/img»)
    """
    known_registries = ("ghcr.io", "gcr.io", "quay.io", "mcr.microsoft.com")
    for reg in known_registries:
        if image.startswith(reg + "/"):
            repo = image[len(reg) + 1 :]
            return reg, repo

    # docker.io (default)
    if "/" not in image:
        # Official image: library/<name>
        return "registry-1.docker.io", f"library/{image}"
    return "registry-1.docker.io", image


def _fetch_tags(image: str) -> list[str] | None:
    """Fetch available tags for *image* from its registry.

    Returns None on any error (caller treats as report-only).
    Supports docker.io and ghcr.io (anonymous).
    """
    host, repo = _normalize_docker_image(image)

    headers: dict[str, str] = {"Accept": "application/json"}

    if host == "registry-1.docker.io":
        token = _docker_hub_token(repo)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://{host}/v2/{repo}/tags/list"
    elif host == "ghcr.io":
        url = f"https://{host}/v2/{repo}/tags/list"
    else:
        # Unknown registry; report-only
        return None

    try:
        req = urllib.request.Request(url, headers=headers)  # noqa: S310
        with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read())
            tags = data.get("tags")
            if isinstance(tags, list):
                return [str(t) for t in tags]
            return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, Exception):
        return None


# ---------------------------------------------------------------------------
# Semver + suffix-aware latest resolution
# ---------------------------------------------------------------------------

_SEMVER_SUFFIX_RE = re.compile(r"^(?P<version>\d+(?:\.\d+)*)(?P<suffix>-[\w.]+)?$")

_NON_SEMVER_TAGS = frozenset({"latest", "stable", "edge", "nightly", "main", "master"})


def _extract_suffix(tag: str) -> str | None:
    """Return the suffix portion of *tag* (e.g. ``-alpine``, ``-slim``), or None."""
    m = _SEMVER_SUFFIX_RE.match(tag)
    if m:
        return m.group("suffix") or ""
    return None


def _is_semver_tag(tag: str) -> bool:
    return bool(_SEMVER_SUFFIX_RE.match(tag)) and tag not in _NON_SEMVER_TAGS


def _resolve_latest_tag(
    current_tag: str,
    available_tags: list[str],
    *,
    allow_cross_major: bool = False,
) -> str | None:
    """Return the best candidate tag for *current_tag* from *available_tags*.

    Matching strategy:
    - Candidates must share the same suffix (e.g. ``-alpine``, ``-slim``, or
      bare numeric) as *current_tag*.
    - Default (conservative): bump within same major only.
    - ``allow_cross_major=True``: pick the global best across all majors.
    - Returns None if no suitable candidate is found or *current_tag* is
      non-semver (report-only caller).
    """
    if not _is_semver_tag(current_tag):
        return None

    suffix = _extract_suffix(current_tag)
    if suffix is None:
        return None

    # Build candidate list: same suffix, semver-parseable
    candidates: list[tuple[tuple[int, ...], str]] = []
    for t in available_tags:
        if not _is_semver_tag(t):
            continue
        t_suffix = _extract_suffix(t)
        if t_suffix != suffix:
            continue
        try:
            ver_part = t[: len(t) - len(suffix)] if suffix else t
            tup = semver_tuple(ver_part)
        except (ValueError, IndexError):
            continue
        candidates.append((tup, t))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    try:
        ver_part = (
            current_tag[: len(current_tag) - len(suffix)] if suffix else current_tag
        )
        current_tup = semver_tuple(ver_part)
    except Exception:
        return None

    best_tup, best_tag = candidates[0]

    if not allow_cross_major:
        # Same major only
        same_major = [
            (tup, t)
            for tup, t in candidates
            if tup and current_tup and tup[0] == current_tup[0]
        ]
        if not same_major:
            return None
        best_tup, best_tag = same_major[0]

    if best_tup <= current_tup:
        return None  # already at latest

    return best_tag


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

_REPORT_ONLY_PREFIX = "[report-only] "


def check_container(ws: Workspace) -> list[Change]:
    """Read-only check: parse image pins and query registries.

    Returns a list of Change objects.  Non-actionable items (SHA-pinned,
    non-semver tags, network errors) use a ``to`` value prefixed with
    ``[report-only]`` so the report layer can distinguish them.

    No external processes are spawned; all network calls use stdlib urllib.
    """
    allow_cross_major = os.environ.get("REPO_OPS_CONTAINER_UPDATE_LATEST", "") == "1"
    changes: list[Change] = []
    seen: set[tuple[str, str | None]] = set()  # (image, tag) dedup

    for cf in find_container_files(ws.path):
        refs = _parse_image_refs(cf)
        for ref in refs:
            if ref["is_stage_ref"]:
                continue
            image: str = ref["image"]
            tag: str | None = ref["tag"]
            sha: str | None = ref["sha"]

            key = (image, tag)
            if key in seen:
                continue
            seen.add(key)

            # SHA-pinned: report only
            if sha and not tag:
                changes.append(
                    Change(
                        name=f"{image}@{sha[:16]}",
                        from_ver="sha-pinned",
                        to=f"{_REPORT_ONLY_PREFIX}sha-pinned; use a tag to enable updates",
                    )
                )
                continue
            if sha and tag:
                # tag+sha: treat as sha-pinned (conservative)
                changes.append(
                    Change(
                        name=f"{image}:{tag}",
                        from_ver=tag,
                        to=f"{_REPORT_ONLY_PREFIX}sha-pinned; digest refresh not yet supported",
                    )
                )
                continue

            if not tag:
                # Implicit "latest" — report only
                changes.append(
                    Change(
                        name=image,
                        from_ver="(no tag)",
                        to=f"{_REPORT_ONLY_PREFIX}no explicit tag; pin to a semver tag",
                    )
                )
                continue

            # Non-semver tag (latest, stable, …) → report only
            if not _is_semver_tag(tag):
                changes.append(
                    Change(
                        name=f"{image}:{tag}",
                        from_ver=tag,
                        to=f"{_REPORT_ONLY_PREFIX}non-semver tag; manual review required",
                    )
                )
                continue

            # Query registry
            available = _fetch_tags(image)
            if available is None:
                changes.append(
                    Change(
                        name=f"{image}:{tag}",
                        from_ver=tag,
                        to=f"{_REPORT_ONLY_PREFIX}registry query failed",
                    )
                )
                continue

            latest = _resolve_latest_tag(
                tag, available, allow_cross_major=allow_cross_major
            )
            if latest is None:
                # Already at latest or indeterminate
                continue

            changes.append(Change(name=f"{image}:{tag}", from_ver=tag, to=latest))

    return changes


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _replace_tag_in_line(line: str, image: str, old_tag: str, new_tag: str) -> str:
    """Byte-preserving single-line tag replacement.

    Replaces only the first occurrence of ``<image>:<old_tag>`` with
    ``<image>:<new_tag>`` in *line*.  Never reflows or reformats the line.
    """
    old = f"{image}:{old_tag}"
    new = f"{image}:{new_tag}"
    # Replace exactly once, at the first occurrence
    if old in line:
        return line.replace(old, new, 1)
    return line


def apply_container(ws: Workspace) -> None:
    """Apply image tag bumps in-place (byte-preserving single-line edits).

    No verify step for v1: building images is out of scope.  The apply
    function runs check_container to discover bumps, then rewrites only the
    tag portion of each matched line — never the entire line.

    SHA-pinned images are skipped unconditionally (report-only by design).
    Cross-major bumps require ``REPO_OPS_CONTAINER_UPDATE_LATEST=1``.
    """
    allow_cross_major = os.environ.get("REPO_OPS_CONTAINER_UPDATE_LATEST", "") == "1"

    for cf in find_container_files(ws.path):
        refs = _parse_image_refs(cf)
        # Build a list of (image, old_tag, new_tag) to apply
        bumps: list[tuple[str, str, str]] = []
        already_queried: dict[tuple[str, str], str | None] = {}

        for ref in refs:
            if ref["is_stage_ref"]:
                continue
            image: str = ref["image"]
            tag: str | None = ref["tag"]
            sha: str | None = ref["sha"]

            if sha or not tag or not _is_semver_tag(tag):
                continue

            cache_key = (image, tag)
            if cache_key in already_queried:
                new_tag = already_queried[cache_key]
            else:
                available = _fetch_tags(image)
                if available is None:
                    already_queried[cache_key] = None
                    continue
                new_tag = _resolve_latest_tag(
                    tag, available, allow_cross_major=allow_cross_major
                )
                already_queried[cache_key] = new_tag

            if new_tag is None:
                continue
            bumps.append((image, tag, new_tag))

        if not bumps:
            continue

        # Apply bumps in-place (byte-preserving, CRLF-safe).
        # Use newline="" so Python does not translate \r\n → \n on read or
        # \n → OS newline on write; the file's own line endings are preserved.
        with cf.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            original = fh.read()
        lines = original.splitlines(keepends=True)
        changed = False
        for image, old_tag, new_tag in bumps:
            for i, line in enumerate(lines):
                updated = _replace_tag_in_line(line, image, old_tag, new_tag)
                if updated != line:
                    lines[i] = updated
                    changed = True
                    break  # one bump = one line

        if changed:
            with cf.open("w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
