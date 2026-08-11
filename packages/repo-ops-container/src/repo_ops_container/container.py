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
  matched line changes; never reflows.
* SHA-pinned images: opt-in digest refresh via ``REPO_OPS_CONTAINER_REFRESH_SHA=1``.
  When enabled, for ``image:tag@sha256:...`` pins, the manifest is queried for
  the tag and the Docker-Content-Digest is compared; if it differs the digest
  is updated keeping the same tag (byte-preserving line edit).
* Compose build.dockerfile: compose services with ``build:`` keys have their
  referenced Dockerfile included in discovery/check/apply (deduped by resolved
  path).
* Registry auth: OCI distribution spec bearer-challenge flow (RFC 6750).
  On 401 from /v2/<name>/tags/list, the WWW-Authenticate header is parsed for
  realm/service/scope, then a token is fetched from that realm (anonymous, or
  authenticated when credentials are resolved — see below). Supports
  docker.io (special endpoint mapping), ghcr.io, quay.io, gcr.io,
  mcr.microsoft.com, and any spec-compliant public OCI registry.
* Credential resolution (``_resolve_auth_for_host``), first match wins:
  1. ``auths[host].auth`` in ``~/.docker/config.json`` (or ``$DOCKER_CONFIG``)
     — inline basic-auth, no subprocess.
  2. Docker credential helper — ``docker-credential-<helper> get`` via
     ``credHelpers[host]`` (per-host) or ``credsStore`` (global fallback),
     e.g. ``docker-credential-ecr-login``, ``docker-credential-osxkeychain``.
  3. ECR fallback — for ``<account>.dkr.ecr.<region>.amazonaws.com`` hosts,
     ``aws ecr get-login-password --region <region>`` when the AWS CLI is on
     PATH and no Docker credential helper is configured.
  All subprocess calls are bounded (5s timeout) and failures are swallowed;
  registries that resolve no credentials fall back to anonymous access, and
  any host that still gets 401-after-token is reported read-only (detail
  string prefix ``[report-only]``).
* Verify: v1 has no verify step; building images is out of scope.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from functools import cache
from pathlib import Path
from typing import Any

from repo_ops_core.holds import (
    HoldRule,
    load_global_holds,
    load_repo_holds,
    resolve_hold,
)
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
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
    """Return all Dockerfile* and compose files in *directory* (non-recursive).

    Also resolves any Dockerfiles referenced via compose ``build:`` keys and
    adds them to the result (deduped by resolved path).
    """
    found: list[Path] = []
    resolved_paths: set[Path] = set()

    try:
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            if _DOCKERFILE_RE.match(entry.name) or entry.name in _COMPOSE_NAMES:
                rp = entry.resolve()
                if rp not in resolved_paths:
                    resolved_paths.add(rp)
                    found.append(entry)
    except PermissionError:
        pass

    # For each compose file found, also collect referenced build Dockerfiles.
    for cf in list(found):
        if cf.name in _COMPOSE_NAMES or "compose" in cf.name.lower():
            for extra in _compose_build_dockerfiles(cf):
                rp = extra.resolve()
                if rp not in resolved_paths:
                    resolved_paths.add(rp)
                    found.append(extra)

    return found


# ---------------------------------------------------------------------------
# Compose build.dockerfile resolution
# ---------------------------------------------------------------------------

# Matches «  build: ./path» (short string form)
_COMPOSE_BUILD_STR_RE = re.compile(
    r'^(?P<indent>[ \t]*)build:\s*(?P<path>["\']?[./][^\s"\']+["\']?)\s*$'
)
# Matches «  build:» (block form — next lines have context/dockerfile keys)
_COMPOSE_BUILD_BLOCK_RE = re.compile(r"^(?P<indent>[ \t]*)build:\s*$")
_COMPOSE_CONTEXT_RE = re.compile(
    r'^[ \t]+context:\s*["\']?(?P<path>[^\s"\']+)["\']?\s*$'
)
_COMPOSE_DOCKERFILE_RE = re.compile(
    r'^[ \t]+dockerfile:\s*["\']?(?P<path>[^\s"\']+)["\']?\s*$'
)


def _compose_build_dockerfiles(compose_path: Path) -> list[Path]:
    """Return Dockerfiles referenced via ``build:`` in *compose_path*.

    Handles:
    - Short string form: ``build: ./context`` → look for Dockerfile in that dir.
    - Mapping form: ``build: { context: ..., dockerfile: ... }``
    """
    base_dir = compose_path.parent
    try:
        text = compose_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    results: list[Path] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        str_m = _COMPOSE_BUILD_STR_RE.match(line)
        if str_m:
            # Short string form: build: ./context — default Dockerfile in that dir
            ctx_path = str_m.group("path").strip("\"'")
            ctx_dir = (base_dir / ctx_path).resolve()
            candidate = ctx_dir / "Dockerfile"
            if candidate.is_file():
                results.append(candidate)
            i += 1
            continue

        block_m = _COMPOSE_BUILD_BLOCK_RE.match(line)
        if block_m:
            # Block form: parse following context/dockerfile lines
            build_indent = block_m.group("indent")
            context_path: str | None = None
            dockerfile_path: str | None = None
            j = i + 1
            while j < len(lines):
                sub = lines[j]
                # Stop when back at same or lesser indentation
                if sub.strip() and not sub.startswith(build_indent + " "):
                    break
                cm = _COMPOSE_CONTEXT_RE.match(sub)
                if cm:
                    context_path = cm.group("path").strip("\"'")
                dm = _COMPOSE_DOCKERFILE_RE.match(sub)
                if dm:
                    dockerfile_path = dm.group("path").strip("\"'")
                j += 1

            # Resolve: context is relative to compose dir; dockerfile is
            # relative to context (or compose dir if no context).
            ctx_dir = base_dir
            if context_path:
                ctx_dir = (base_dir / context_path).resolve()
            if dockerfile_path:
                candidate = (ctx_dir / dockerfile_path).resolve()
            else:
                candidate = ctx_dir / "Dockerfile"

            if candidate.is_file():
                results.append(candidate)
            i = j
            continue

        i += 1

    return results


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
# Registry HTTP client (stdlib urllib)
# ---------------------------------------------------------------------------

_REGISTRY_TIMEOUT = 10  # seconds

# OCI manifest media types for digest HEAD/GET
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

# docker.io special endpoint mapping
_DOCKER_IO_ALIASES = frozenset(
    {"docker.io", "index.docker.io", "registry.docker.io", "registry-1.docker.io"}
)
_DOCKER_REGISTRY_HOST = "registry-1.docker.io"
_DOCKER_AUTH_REALM = "https://auth.docker.io/token"
_DOCKER_AUTH_SERVICE = "registry.docker.io"


def _normalize_docker_image(image: str) -> tuple[str, str]:
    """Return (registry_host, repo_path) for an image reference.

    Examples:
      ``nginx``            → (``registry-1.docker.io``, ``library/nginx``)
      ``user/app``         → (``registry-1.docker.io``, ``user/app``)
      ``ghcr.io/org/img``  → (``ghcr.io``, ``org/img``)
    """
    # Detect registry prefix: contains a dot or colon before the first slash,
    # or is a known docker.io alias.
    slash = image.find("/")
    if slash != -1:
        prefix = image[:slash]
        rest = image[slash + 1 :]
        # Is it a host-like prefix? (contains a dot, colon, or is "localhost")
        if "." in prefix or ":" in prefix or prefix == "localhost":
            host = prefix
            if host in _DOCKER_IO_ALIASES:
                # docker.io library images: docker.io/nginx → library/nginx
                if "/" not in rest:
                    return _DOCKER_REGISTRY_HOST, f"library/{rest}"
                return _DOCKER_REGISTRY_HOST, rest
            return host, rest
        # No dot/colon: user/image on docker.io
        return _DOCKER_REGISTRY_HOST, image

    # No slash at all — official library image on docker.io
    return _DOCKER_REGISTRY_HOST, f"library/{image}"


# ---------------------------------------------------------------------------
# Docker config.json credential helpers
# ---------------------------------------------------------------------------


def _docker_config_path() -> Path:
    """Return the path to ~/.docker/config.json (or $DOCKER_CONFIG/config.json)."""
    docker_config_env = os.environ.get("DOCKER_CONFIG", "")
    if docker_config_env:
        return Path(docker_config_env) / "config.json"
    return Path.home() / ".docker" / "config.json"


def _load_docker_config() -> dict[str, Any]:
    """Load ~/.docker/config.json; return empty dict on any error."""
    cfg_path = _docker_config_path()
    try:
        return dict(json.loads(cfg_path.read_text(encoding="utf-8")))
    except Exception:
        return {}


def _docker_config_host_keys(host: str) -> list[str]:
    """Candidate ``auths`` / ``credHelpers`` keys for *host*.

    Docker Hub normalizes to ``registry-1.docker.io`` for OCI calls, but
    config.json commonly keys helpers/auths as ``docker.io`` or
    ``https://index.docker.io/v1/``.
    """
    candidates = [host]
    if host == _DOCKER_REGISTRY_HOST:
        candidates += [*_DOCKER_IO_ALIASES, "https://index.docker.io/v1/"]
    return candidates


def _cred_helper_get_server(host: str) -> str:
    """Registry URL passed on stdin to ``docker-credential-* get``.

    Docker Hub helpers expect the canonical Hub login URL, not the OCI host.
    """
    if host == _DOCKER_REGISTRY_HOST or host in _DOCKER_IO_ALIASES:
        return "https://index.docker.io/v1/"
    return host


def _basic_auth_for_host(host: str) -> str | None:
    """Return ``Authorization: Basic <b64>`` value for *host* from docker config.

    Only handles the ``auths[host].auth`` (base64 user:pass) entry.
    Credential helpers are resolved separately by ``_resolve_auth_for_host``.
    Returns None when no matching entry, no ``auth`` key, or only a helper.
    """
    cfg = _load_docker_config()
    auths: Any = cfg.get("auths", {})
    if not isinstance(auths, dict):
        return None

    for key in _docker_config_host_keys(host):
        entry = auths.get(key)
        if not isinstance(entry, dict):
            # Try with https:// prefix (some tools store that way)
            entry = auths.get(f"https://{key}")
            if not isinstance(entry, dict):
                continue
        auth_b64: Any = entry.get("auth")
        if auth_b64 and isinstance(auth_b64, str):
            return f"Basic {auth_b64}"

    return None


# ---------------------------------------------------------------------------
# Credential-helper resolution (ECR, docker-credential-* helpers)
# ---------------------------------------------------------------------------

# Timeout for credential helper subprocesses (kept short; helpers should be fast).
_CRED_HELPER_TIMEOUT = 5  # seconds

# ECR registry hostname pattern: <account>.dkr.ecr.<region>.amazonaws.com
_ECR_HOST_RE = re.compile(
    r"^(?P<account>\d+)\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com$"
)


def _ecr_region_for_host(host: str) -> str | None:
    """Return the AWS region for an ECR registry host, or None if not ECR."""
    m = _ECR_HOST_RE.match(host)
    return m.group("region") if m else None


def _cred_helper_name_for_host(host: str) -> str | None:
    """Return the Docker credential helper name configured for *host*, or None.

    Checks ``credHelpers[host]`` first (per-host override, including Docker Hub
    aliases when *host* is ``registry-1.docker.io``), then ``credsStore``
    (global fallback).  Returns the bare helper name *without* the
    ``docker-credential-`` prefix (e.g. ``"ecr-login"``, ``"osxkeychain"``).
    Returns None when neither key is configured.
    """
    cfg = _load_docker_config()
    cred_helpers: Any = cfg.get("credHelpers", {})
    if isinstance(cred_helpers, dict):
        for key in _docker_config_host_keys(host):
            helper = cred_helpers.get(key)
            if isinstance(helper, str) and helper:
                return helper
            # Try with https:// prefix (some tools store that way)
            helper = cred_helpers.get(f"https://{key}")
            if isinstance(helper, str) and helper:
                return helper
    creds_store: Any = cfg.get("credsStore")
    if isinstance(creds_store, str) and creds_store:
        return creds_store
    return None


def _run_cred_helper(helper_name: str, host: str) -> str | None:
    """Run ``docker-credential-<helper> get`` and return a ``Basic`` auth value.

    Docker credential-helper protocol (per spec):
    - stdin:  the registry hostname (plain hostname, no ``https://``)
    - stdout: JSON ``{"Username": "...", "Secret": "..."}``

    Returns ``"Basic <b64(user:secret)>"`` on success, None on any failure
    (binary absent, non-zero exit, bad JSON, empty fields).  All errors are
    swallowed so callers fall back gracefully to anonymous access.
    """
    binary = f"docker-credential-{helper_name}"
    try:
        proc = subprocess.run(
            [binary, "get"],
            input=host,
            capture_output=True,
            text=True,
            timeout=_CRED_HELPER_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
        username = str(data.get("Username") or "")
        secret = str(data.get("Secret") or "")
        if not username or not secret:
            return None
        b64 = base64.b64encode(f"{username}:{secret}".encode()).decode()
        return f"Basic {b64}"
    except (json.JSONDecodeError, ValueError):
        return None


def _ecr_get_login_password(region: str) -> str | None:
    """Obtain ECR credentials via ``aws ecr get-login-password --region <region>``.

    Alternative path when ``docker-credential-ecr-login`` is absent but the
    AWS CLI is available.  ECR username is always ``"AWS"``.

    Returns ``"Basic <b64(AWS:password)>"`` on success, None on any error.
    Failures are swallowed so the caller falls back to anonymous access.
    """
    try:
        proc = subprocess.run(
            ["aws", "ecr", "get-login-password", "--region", region],
            capture_output=True,
            text=True,
            timeout=_CRED_HELPER_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    password = proc.stdout.strip()
    if not password:
        return None
    b64 = base64.b64encode(f"AWS:{password}".encode()).decode()
    return f"Basic {b64}"


def _resolve_auth_for_host(host: str) -> str | None:
    """Return the best ``Authorization: Basic <b64>`` value for *host*.

    Resolution order (first non-None result wins):

    1. ``auths[host].auth`` in docker config.json — the existing in-process
       path; no subprocess spawned.
    2. ``docker-credential-<helper> get`` via ``credHelpers[host]`` or
       ``credsStore`` from docker config.json.
    3. ``aws ecr get-login-password`` when *host* is an ECR endpoint — used
       when no Docker credential helper is wired but the AWS CLI is available.

    All subprocess calls are bounded by ``_CRED_HELPER_TIMEOUT`` and swallowed
    on failure, so the caller always falls back to anonymous / bearer-only
    access gracefully.
    """
    # 1. Inline auth (no subprocess)
    auth = _basic_auth_for_host(host)
    if auth:
        return auth

    # 2. Docker credential helper (opt-in: only when helper is configured)
    helper = _cred_helper_name_for_host(host)
    if helper:
        auth = _run_cred_helper(helper, _cred_helper_get_server(host))
        if auth:
            return auth

    # 3. ECR fallback via aws CLI (opt-in: only for ECR hosts + aws on PATH)
    region = _ecr_region_for_host(host)
    if region:
        auth = _ecr_get_login_password(region)
        if auth:
            return auth

    return None


# ---------------------------------------------------------------------------
# Generic OCI bearer-challenge token fetch
# ---------------------------------------------------------------------------


def _parse_www_authenticate(header_value: str) -> dict[str, str]:
    """Parse a ``WWW-Authenticate: Bearer realm=...,service=...,scope=...`` header.

    Returns a dict with the parsed key=value pairs (realm, service, scope).
    """
    # Strip "Bearer " prefix
    value = re.sub(r"^Bearer\s+", "", header_value, flags=re.IGNORECASE)
    result: dict[str, str] = {}
    # Parse key="value" or key=value pairs
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([\w:/.@%-]+))', value):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else m.group(3)
        result[key] = val
    return result


def _fetch_bearer_token(
    realm: str,
    service: str,
    scope: str,
    basic_auth: str | None = None,
) -> str | None:
    """Fetch a bearer token from *realm* for *service*/*scope*.

    Uses anonymous access unless *basic_auth* is provided (``Basic <b64>``).
    """
    # Built manually (not via urllib.parse.urlencode(..., quote_via=...)) so
    # both space-as-%20 (not quote_plus's "+") and the exact `quote()` safe=""
    # encoding stay explicit, without fighting typeshed's overloaded
    # `_QuoteVia` protocol for a plain function reference.
    params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in {"service": service, "scope": scope}.items()
    )
    url = f"{realm}?{params}"
    headers: dict[str, str] = {"Accept": "application/json"}
    if basic_auth:
        headers["Authorization"] = basic_auth
    try:
        req = urllib.request.Request(url, headers=headers)  # noqa: S310
        with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # noqa: S310
            data: dict[str, Any] = json.loads(resp.read())
            token = data.get("token") or data.get("access_token")
            return str(token) if token else None
    except Exception:
        return None


def _fetch_token_for_registry(
    host: str, repo: str, basic_auth: str | None
) -> str | None:
    """Fetch an anonymous/basic-auth bearer token using the OCI challenge flow.

    For docker.io: uses the known auth endpoint directly (avoids an extra RTT).
    For all other registries: does the standard 401 challenge probe on /v2/.
    """
    if host == _DOCKER_REGISTRY_HOST:
        scope = f"repository:{repo}:pull"
        return _fetch_bearer_token(
            _DOCKER_AUTH_REALM,
            _DOCKER_AUTH_SERVICE,
            scope,
            basic_auth,
        )

    # Generic: probe /v2/ to trigger the 401 challenge.
    probe_url = f"https://{host}/v2/"
    try:
        req = urllib.request.Request(probe_url, headers={"Accept": "application/json"})
        urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT)  # noqa: S310
        # 200 OK → no auth needed (unauthenticated push, rare)
        return None
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            return None
        www_auth = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
        if not www_auth:
            return None
        params = _parse_www_authenticate(www_auth)
        realm = params.get("realm", "")
        service = params.get("service", host)
        # Use repo-scoped scope from the challenge or build a default
        scope = params.get("scope", f"repository:{repo}:pull")
        if not realm:
            return None
        return _fetch_bearer_token(realm, service, scope, basic_auth)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tags fetch (generic registry)
# ---------------------------------------------------------------------------


def _fetch_tags(image: str) -> list[str] | None:
    """Fetch available tags for *image* from its registry.

    Returns None on any error (caller treats as report-only).

    Supports any OCI distribution-spec compliant registry using the standard
    bearer-challenge flow.  docker.io's ``library/`` normalization and
    ``registry-1`` endpoint mapping are preserved.  For private registries
    (ECR, credential-helper-backed) ``_resolve_auth_for_host`` is called to
    obtain credentials before the bearer-challenge flow.
    """
    host, repo = _normalize_docker_image(image)
    basic_auth = _resolve_auth_for_host(host)

    token = _fetch_token_for_registry(host, repo, basic_auth)

    headers: dict[str, str] = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif basic_auth:
        headers["Authorization"] = basic_auth

    url = f"https://{host}/v2/{repo}/tags/list"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read())
            tags = data.get("tags")
            if isinstance(tags, list):
                return [str(t) for t in tags]
            return None
    except Exception:
        # Bare catch-all is intentional (matches this function's "returns None
        # on any error" contract): URLError/OSError/JSONDecodeError are all
        # subclasses of Exception and would never be reached ahead of it, but
        # non-dict JSON bodies (AttributeError on .get) and malformed URLs
        # (ValueError) are not covered by those three alone.
        return None


# ---------------------------------------------------------------------------
# Digest refresh (OCI manifest HEAD/GET)
# ---------------------------------------------------------------------------


def _fetch_manifest_digest(image: str, tag: str) -> str | None:
    """HEAD the manifest for *image*:*tag* and return the Docker-Content-Digest.

    Returns None on any error (caller treats as report-only).
    Uses the same auth flow as _fetch_tags (including credential helpers).
    """
    host, repo = _normalize_docker_image(image)
    basic_auth = _resolve_auth_for_host(host)
    token = _fetch_token_for_registry(host, repo, basic_auth)

    headers: dict[str, str] = {"Accept": _MANIFEST_ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif basic_auth:
        headers["Authorization"] = basic_auth

    url = f"https://{host}/v2/{repo}/manifests/{tag}"
    # Try HEAD first (cheaper), fall back to GET if HEAD not supported
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=_REGISTRY_TIMEOUT) as resp:  # noqa: S310
                digest = resp.headers.get("Docker-Content-Digest", "")
                if digest:
                    return str(digest)
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (404, 405):
                continue  # try GET
            return None
        except Exception:
            return None
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
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------


@cache
def _global_holds_cached() -> tuple[HoldRule, ...]:
    return tuple(load_global_holds())


@cache
def _repo_holds_cached(repo_root: str) -> tuple[HoldRule, ...]:
    return tuple(load_repo_holds(Path(repo_root)))


def _workspace_rel(ws: Workspace) -> str:
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve()).as_posix()
    except ValueError:
        return "."
    return rel or "."


def _resolve_container_hold(ws: Workspace, package: str) -> HoldRule | None:
    """Return the active container hold for *package*, if any."""
    repo_root = str(ws.git_repo.resolve())
    rules = [*_global_holds_cached(), *_repo_holds_cached(repo_root)]
    if not rules:
        return None
    return resolve_hold(
        rules,
        package=_canonical_hold_package(package),
        ecosystem="container",
        manager=ws.manager,
        workspace_rel=_workspace_rel(ws),
    )


def _canonical_hold_package(package: str) -> str:
    """Return the stable package key used for container hold matching."""
    host, repo = _normalize_docker_image(package)
    if host == _DOCKER_REGISTRY_HOST:
        host = "docker.io"
    return f"{host}/{repo}"


def _tag_version_for_hold(tag: str) -> str | None:
    match = _SEMVER_SUFFIX_RE.match(tag)
    return match.group("version") if match else None


def _target_within_hold(ws: Workspace, package: str, target_tag: str) -> bool:
    """Return whether a semver tag target is allowed by its active hold."""
    rule = _resolve_container_hold(ws, package)
    tag_version = _tag_version_for_hold(target_tag)
    return (
        rule is None
        or tag_version is None
        or version_satisfies_ceiling(tag_version, rule.max_version)
    )


def _resolve_hold_tag_target(
    current_tag: str, tags: list[str], rule: HoldRule
) -> str | None:
    """Return the highest tag at or below *rule* when *current_tag* violates it."""
    current_version = _tag_version_for_hold(current_tag)
    if current_version is None:
        return None
    try:
        current_tup = semver_tuple(current_version)
    except Exception:
        return None
    if version_satisfies_ceiling(current_version, rule.max_version):
        return None

    suffix = _extract_suffix(current_tag)
    if suffix is None:
        return None
    candidates: list[tuple[tuple[int, ...], str]] = []
    for tag in tags:
        if not _is_semver_tag(tag) or _extract_suffix(tag) != suffix:
            continue
        tag_version = _tag_version_for_hold(tag)
        if tag_version is None:
            continue
        try:
            tag_tup = semver_tuple(tag_version)
        except Exception:
            continue
        if version_satisfies_ceiling(tag_version, rule.max_version):
            candidates.append((tag_tup, tag))

    if not candidates:
        return None
    target_tup, target_tag = max(candidates, key=lambda item: (item[0], item[1]))
    if target_tup >= current_tup:
        return None
    return target_tag


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

_REPORT_ONLY_PREFIX = "[report-only] "


def check_container(ws: Workspace) -> list[Change]:
    """Read-only check: parse image pins and query registries.

    Returns a list of Change objects.  Non-actionable items (SHA-pinned,
    non-semver tags, network errors) use a ``to`` value prefixed with
    ``[report-only]`` so the report layer can distinguish them.

    Registry queries use stdlib urllib.  Credential resolution may spawn
    bounded subprocesses (``docker-credential-*``, ``aws ecr get-login-password``)
    when helpers are configured; see ``_resolve_auth_for_host``.

    Environment variables:
    - ``REPO_OPS_CONTAINER_UPDATE_LATEST=1``: allow cross-major bumps.
    - ``REPO_OPS_CONTAINER_REFRESH_SHA=1``: for ``image:tag@sha256:...`` pins,
      query the manifest and report/apply a digest update if it has changed.
    """
    allow_cross_major = os.environ.get("REPO_OPS_CONTAINER_UPDATE_LATEST", "") == "1"
    refresh_sha = os.environ.get("REPO_OPS_CONTAINER_REFRESH_SHA", "") == "1"
    changes: list[Change] = []
    # (image, tag, sha) dedup — sha must be part of the key so multi-stage
    # files that pin the same image:tag at different digests get each
    # occurrence checked independently (one stage can be current while
    # another has drifted).
    seen: set[tuple[str, str | None, str | None]] = set()
    # Registry digest lookups are still deduped per (image, tag): every
    # occurrence resolves to the same upstream digest.
    digest_cache: dict[tuple[str, str], str | None] = {}

    for cf in find_container_files(ws.path):
        refs = _parse_image_refs(cf)
        for ref in refs:
            if ref["is_stage_ref"]:
                continue
            image: str = ref["image"]
            tag: str | None = ref["tag"]
            sha: str | None = ref["sha"]

            key = (image, tag, sha)
            if key in seen:
                continue
            seen.add(key)

            # SHA-pinned (no tag): report only
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
                # tag+sha pin: attempt digest refresh when opt-in
                if refresh_sha:
                    cache_key = (image, tag)
                    if cache_key in digest_cache:
                        new_digest = digest_cache[cache_key]
                    else:
                        new_digest = _fetch_manifest_digest(image, tag)
                        digest_cache[cache_key] = new_digest
                    if new_digest is None:
                        changes.append(
                            Change(
                                name=f"{image}:{tag}",
                                from_ver=sha,
                                to=f"{_REPORT_ONLY_PREFIX}digest query failed",
                            )
                        )
                    elif new_digest == sha:
                        # Already current; no change needed
                        pass
                    else:
                        if not _target_within_hold(ws, image, tag):
                            continue
                        # Digest has changed — actionable update
                        changes.append(
                            Change(
                                name=f"{image}:{tag}",
                                from_ver=sha,
                                to=new_digest,
                            )
                        )
                else:
                    changes.append(
                        Change(
                            name=f"{image}:{tag}",
                            from_ver=tag,
                            to=f"{_REPORT_ONLY_PREFIX}sha-pinned; set REPO_OPS_CONTAINER_REFRESH_SHA=1 to refresh digest",
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

            rule = _resolve_container_hold(ws, image)
            if rule is not None:
                clamp_target = _resolve_hold_tag_target(tag, available, rule)
                if clamp_target is not None:
                    changes.append(
                        Change(
                            name=f"{image}:{tag}",
                            from_ver=tag,
                            to=clamp_target,
                        )
                    )
                    continue

            latest = _resolve_latest_tag(
                tag, available, allow_cross_major=allow_cross_major
            )
            if latest is None:
                # Already at latest or indeterminate
                continue

            if not _target_within_hold(ws, image, latest):
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

    A match is only accepted at a tag boundary: the character immediately
    following ``old_tag`` must not continue the tag (digit/letter/``.``/``_``/
    ``-``), so bumping ``image:1`` never fires on a line containing
    ``image:1.5``.
    """
    old = f"{image}:{old_tag}"
    new = f"{image}:{new_tag}"
    start = 0
    while True:
        idx = line.find(old, start)
        if idx == -1:
            return line
        end = idx + len(old)
        if end < len(line) and (line[end].isalnum() or line[end] in "._-"):
            start = idx + 1
            continue
        return line[:idx] + new + line[end:]


def _replace_digest_in_line(
    line: str, image: str, tag: str, old_sha: str, new_sha: str
) -> str:
    """Byte-preserving single-line digest replacement.

    Replaces the sha256 digest in ``<image>:<tag>@<old_sha>`` with *new_sha*.
    Never reflows or reformats the line.
    """
    old = f"{image}:{tag}@{old_sha}"
    new = f"{image}:{tag}@{new_sha}"
    if old in line:
        return line.replace(old, new, 1)
    return line


def _replace_image_ref_in_line(
    line: str,
    image: str,
    old_tag: str,
    old_sha: str,
    new_tag: str,
    new_sha: str,
) -> str:
    """Replace a held ``<image>:<tag>@<sha>`` reference in one line."""
    old = f"{image}:{old_tag}@{old_sha}"
    new = f"{image}:{new_tag}@{new_sha}"
    if old in line:
        return line.replace(old, new, 1)
    return line


def _enforce_version_ceilings(
    ws: Workspace, refs: list[dict[str, Any]]
) -> dict[int, tuple[str, str, str | None, str, str | None]]:
    """Return line replacements that clamp held container image refs."""
    line_bumps: dict[int, tuple[str, str, str | None, str, str | None]] = {}
    for ref in refs:
        image: str = ref["image"]
        current_tag: str | None = ref["tag"]
        current_sha: str | None = ref["sha"]
        line_no: int = ref["line_no"]
        if not current_tag or _tag_version_for_hold(current_tag) is None:
            continue

        rule = _resolve_container_hold(ws, image)
        if rule is None:
            continue
        current_version = _tag_version_for_hold(current_tag)
        if current_version is None or version_satisfies_ceiling(
            current_version, rule.max_version
        ):
            continue

        tags = _fetch_tags(image)
        if tags is None:
            raise RuntimeError(
                f"hold: failed to clamp {image} back to <={rule.max_version} "
                "(container): tag fetch failed while resolving image pin"
            )
        target_tag = _resolve_hold_tag_target(current_tag, tags, rule)
        if target_tag is None:
            raise RuntimeError(
                f"hold: failed to clamp {image} back to <={rule.max_version} "
                "(container): no available tag satisfies ceiling"
            )

        target_sha: str | None = None
        if current_sha is not None:
            target_sha = _fetch_manifest_digest(image, target_tag)
            if target_sha is None:
                raise RuntimeError(
                    f"hold: failed to clamp {image} back to <={rule.max_version} "
                    "(container): digest query failed for held tag"
                )
        line_bumps[line_no] = (
            image,
            current_tag,
            current_sha,
            target_tag,
            target_sha,
        )
    return line_bumps


def apply_container(ws: Workspace) -> None:
    """Apply image tag bumps and digest refreshes in-place (byte-preserving single-line edits).

    No verify step for v1: building images is out of scope.  Reuses
    ``check_container`` for discovery so plan and apply share one resolution
    path, then rewrites only the tag or digest portion of each matched line.

    SHA-pinned images without a tag are skipped unconditionally (report-only
    by design).  Digest refresh requires ``REPO_OPS_CONTAINER_REFRESH_SHA=1``.
    Cross-major bumps require ``REPO_OPS_CONTAINER_UPDATE_LATEST=1``.
    """
    # Reuse check_container so tag/digest resolution cannot drift from plan.
    tag_bumps: list[tuple[str, str, str]] = []
    sha_bumps: list[tuple[str, str, str, str]] = []
    for ch in check_container(ws):
        if ch.to.startswith(_REPORT_ONLY_PREFIX):
            continue
        image, tag = ch.name.rsplit(":", 1)
        if ch.from_ver.startswith("sha256:"):
            sha_bumps.append((image, tag, ch.from_ver, ch.to))
        else:
            tag_bumps.append((image, ch.from_ver, ch.to))

    for cf in find_container_files(ws.path):
        # Apply bumps in-place (byte-preserving, CRLF-safe).
        # Use newline="" so Python does not translate \r\n → \n on read or
        # \n → OS newline on write; the file's own line endings are preserved.
        with cf.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            original = fh.read()
        lines = original.splitlines(keepends=True)
        changed = False
        hold_bumps = _enforce_version_ceilings(ws, _parse_image_refs(cf))

        for line_no, (image, old_tag, old_sha, new_tag, new_sha) in hold_bumps.items():
            i = line_no - 1
            if not 0 <= i < len(lines):
                continue
            if old_sha is None:
                updated = _replace_tag_in_line(lines[i], image, old_tag, new_tag)
            elif new_sha is not None:
                updated = _replace_image_ref_in_line(
                    lines[i], image, old_tag, old_sha, new_tag, new_sha
                )
            else:
                continue
            if updated != lines[i]:
                lines[i] = updated
                changed = True

        # Patch every line carrying the outdated ref, not just the first:
        # multi-stage files legitimately pin the same base image at more
        # than one FROM line, and each occurrence must be updated.
        for image, old_tag, new_tag in tag_bumps:
            for i, line in enumerate(lines):
                updated = _replace_tag_in_line(line, image, old_tag, new_tag)
                if updated != line:
                    lines[i] = updated
                    changed = True

        for image, tag, old_sha, new_sha in sha_bumps:
            for i, line in enumerate(lines):
                updated = _replace_digest_in_line(line, image, tag, old_sha, new_sha)
                if updated != line:
                    lines[i] = updated
                    changed = True

        if changed:
            with cf.open("w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))


# ---------------------------------------------------------------------------
# Public API. Underscored helpers remain importable for unit tests.
# ---------------------------------------------------------------------------

__all__ = [
    "apply_container",
    "check_container",
    "find_container_files",
    "is_container_workspace",
]
