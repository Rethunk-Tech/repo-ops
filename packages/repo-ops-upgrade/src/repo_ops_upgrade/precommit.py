"""pre-commit rev: pin detection, plan, and apply.

Design notes
------------
* Discovery: a directory is a pre-commit workspace when it contains
  ``.pre-commit-config.yaml`` or ``.pre-commit-config.yml``.
* Check (read-only): parses each ``repos[].rev:`` value, queries the upstream
  repo for available tags via ``git ls-remote --tags --refs <url>``, and
  returns Change objects.  Network/subprocess failures and non-semver pins
  (SHAs, branch names) are recorded report-only.
* Apply: byte-preserving single-line ``rev:`` replacement — only the value
  portion of the matched ``rev:`` line is rewritten; indentation and surrounding
  content are preserved.
* Tag matching: the latest tag that shares the same prefix style (``v``-prefixed
  vs bare-numeric) and the same major version (conservative) is selected.  Set
  ``REPO_OPS_PRECOMMIT_UPDATE_LATEST=1`` to allow cross-major bumps.
* All subprocess and network calls are bounded; failures degrade gracefully to
  report-only entries so the check never raises.

Wire-up notes
-------------
To enable this ecosystem in the main upgrade loop, add two lines to
``packages/repo-ops/src/repo_ops/_register.py``::

    from repo_ops_core._register import register_precommit
    register_precommit()

and add a ``consider_precommit(mdir)`` call inside
``repo_ops_upgrade.discovery.discover_workspaces_in_root`` (similar to
``consider_container``).
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from repo_ops_core.config import (
    ambient_env,
    load_global_config,
    load_repo_config,
    process_env_cli_override,
    resolve_setting,
)
from repo_ops_core.holds import HoldRule, resolve_hold
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_models import Change, Workspace

from repo_ops_upgrade import holds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRECOMMIT_CONFIG_NAMES = frozenset(
    {
        ".pre-commit-config.yaml",
        ".pre-commit-config.yml",
    }
)

_REPORT_ONLY_PREFIX = "[report-only] "

# Env-opt-in for cross-major bumps (mirrors REPO_OPS_CONTAINER_UPDATE_LATEST)
_UPDATE_LATEST_ENV = "REPO_OPS_PRECOMMIT_UPDATE_LATEST"

# Env-opt-in for refreshing SHA-pinned hooks within their tagged major.
_REFRESH_SHA_ENV = "REPO_OPS_PRECOMMIT_REFRESH_SHA"

# Timeout for git ls-remote (seconds); kept modest since it's per unique repo.
_LS_REMOTE_TIMEOUT = 30

logger = logging.getLogger(__name__)

# A rev: value that is a hex commit SHA (7-40 hex chars).
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)

# A rev: value that looks like a semver tag (with or without a leading "v").
_SEMVER_TAG_RE = re.compile(r"^v?(\d+)(?:\.\d+)*(?:[-.][\w.]+)?$")

# Regex to match "- repo: <url>" lines (any leading whitespace + list bullet).
_REPO_LINE_RE = re.compile(r"^\s*-\s+repo:\s+(.+?)\s*$")

# Regex to match "  rev: <value>" lines (leading whitespace required).
_REV_LINE_RE = re.compile(r"^(\s+)rev:\s+(.+?)\s*$")


def _precommit_update_latest_enabled(ws: Workspace) -> bool:
    raw = resolve_setting(
        "precommit_update_latest",
        cli_value=process_env_cli_override(_UPDATE_LATEST_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo),
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _precommit_refresh_sha_enabled(ws: Workspace) -> bool:
    raw = resolve_setting(
        "precommit_refresh_sha",
        cli_value=process_env_cli_override(_REFRESH_SHA_ENV),
        env=ambient_env() or None,
        repo_cfg=load_repo_config(ws.git_repo),
        global_cfg=load_global_config(),
    )
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Workspace detection
# ---------------------------------------------------------------------------


def is_precommit_workspace(directory: Path) -> bool:
    """True when *directory* contains a ``.pre-commit-config.yaml`` file."""
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.name in _PRECOMMIT_CONFIG_NAMES:
                return True
    except PermissionError:
        pass
    return False


def _find_precommit_config(directory: Path) -> Path | None:
    """Return the first pre-commit config file found in *directory*, or None."""
    try:
        for entry in directory.iterdir():
            if entry.is_file() and entry.name in _PRECOMMIT_CONFIG_NAMES:
                return entry
    except PermissionError:
        pass
    return None


# ---------------------------------------------------------------------------
# Config parsing (no PyYAML dependency)
# ---------------------------------------------------------------------------


def _parse_precommit_repos(text: str) -> list[dict[str, Any]]:
    """Parse the ``repos:`` section of a ``.pre-commit-config.yaml`` text.

    Extracts ``(repo_url, rev, rev_line_no)`` for each repo entry that has a
    ``rev:`` value.  Uses regex line-by-line parsing (no PyYAML) since the
    format is highly regular: ``- repo: <url>`` followed by ``  rev: <value>``.

    Returns a list of dicts with keys:
      ``repo`` (str), ``rev`` (str), ``rev_line_no`` (int, 1-indexed).
    """
    lines = text.splitlines()

    # 1. Collect positions of all "- repo: <url>" lines.
    repo_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _REPO_LINE_RE.match(line)
        if m:
            repo_positions.append((i, m.group(1).strip()))

    # 2. For each repo, find the first "  rev: <value>" between it and the next.
    results: list[dict[str, Any]] = []
    for j, (line_idx, repo_url) in enumerate(repo_positions):
        end = repo_positions[j + 1][0] if j + 1 < len(repo_positions) else len(lines)
        for k in range(line_idx + 1, end):
            m2 = _REV_LINE_RE.match(lines[k])
            if m2:
                rev = m2.group(2).strip().strip("'\"")
                results.append(
                    {
                        "repo": repo_url,
                        "rev": rev,
                        "rev_line_no": k + 1,  # 1-indexed for error messages
                    }
                )
                break

    return results


# ---------------------------------------------------------------------------
# Rev classification
# ---------------------------------------------------------------------------


def _is_sha_rev(rev: str) -> bool:
    """True when *rev* looks like a hex commit SHA (7-40 hex chars)."""
    return bool(_SHA_RE.match(rev))


def _is_semver_like(tag: str) -> bool:
    """True when *tag* looks like a semver version (bare or v-prefixed)."""
    return bool(_SEMVER_TAG_RE.match(tag))


def _has_v_prefix(tag: str) -> bool:
    return tag.startswith("v")


def _repo_short_name(repo_url: str) -> str:
    """Return a short ``owner/repo`` label for a URL, or the URL itself."""
    # e.g. https://github.com/pre-commit/pre-commit-hooks → pre-commit/pre-commit-hooks
    m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", repo_url)
    return m.group(1) if m else repo_url


# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------


def _workspace_rel(ws: Workspace) -> str:
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve()).as_posix()
    except ValueError:
        return "."
    return rel or "."


def _resolve_precommit_hold(ws: Workspace, package: str) -> HoldRule | None:
    """Return the active pre-commit hold for *package*, if any."""
    repo_root = str(ws.git_repo.resolve())
    rules = [*holds.global_holds_cached(), *holds.repo_holds_cached(repo_root)]
    if not rules:
        return None
    return resolve_hold(
        rules,
        package=package,
        ecosystem="precommit",
        manager=ws.manager,
        workspace_rel=_workspace_rel(ws),
    )


def _target_within_hold(ws: Workspace, package: str, target_version: str) -> bool:
    """Return whether a resolved tag target is allowed by its active hold."""
    return holds.target_within_hold(
        target_version, _resolve_precommit_hold(ws, package)
    )


# ---------------------------------------------------------------------------
# Latest tag resolution
# ---------------------------------------------------------------------------


def _resolve_latest_precommit_rev(
    current_rev: str,
    tags: list[str],
    *,
    allow_cross_major: bool = False,
) -> str | None:
    """Return the best candidate tag for *current_rev* from *tags*.

    Matching rules:
    - Only semver-like tags are considered.
    - Tag must share the same ``v``-prefix style as *current_rev*.
    - Default (conservative): same major only.
    - ``allow_cross_major=True``: pick the global best across all majors.
    - Returns None when already at latest, or no suitable candidate exists.
    """
    if not _is_semver_like(current_rev):
        return None

    want_v = _has_v_prefix(current_rev)
    current_bare = current_rev.lstrip("v")

    try:
        current_tup = semver_tuple(current_bare)
    except Exception:
        return None

    # Build candidate list: same prefix style + parseable semver.
    candidates: list[tuple[tuple[int, ...], str]] = []
    for tag in tags:
        if not _is_semver_like(tag):
            continue
        if _has_v_prefix(tag) != want_v:
            continue
        bare = tag.lstrip("v")
        try:
            tup = semver_tuple(bare)
        except Exception:  # noqa: S112
            continue
        candidates.append((tup, tag))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    if not allow_cross_major:
        # Same major only
        same_major = [
            (tup, t)
            for tup, t in candidates
            if tup and current_tup and tup[0] == current_tup[0]
        ]
        if not same_major:
            return None
        candidates = same_major

    best_tup, best_tag = candidates[0]
    if best_tup <= current_tup:
        return None  # already at latest

    return best_tag


# ---------------------------------------------------------------------------
# Tag fetching (git ls-remote)
# ---------------------------------------------------------------------------

# In-process caches so repeated checks for the same hook repo cost only one RTT.
# _TAG_CACHE_LOCK serialises all reads and writes so concurrent ThreadPoolExecutor
# workers for the same URL each pay the subprocess cost at most once (Bug 2 fix).
_TAG_CACHE: dict[str, list[str] | None] = {}
_TAG_REF_CACHE: dict[str, list[tuple[str, str]] | None] = {}
_TAG_CACHE_LOCK = threading.Lock()


def _fetch_tags_for_repo(
    repo_url: str, *, timeout: int = _LS_REMOTE_TIMEOUT
) -> list[str] | None:
    """Return the list of annotated tags for *repo_url* via ``git ls-remote``.

    Uses an in-process cache protected by a threading.Lock so concurrent callers
    for the same URL only pay the subprocess cost once (cache-stampede safe).
    Returns None on any error (subprocess failure, git not on PATH, etc.).
    """
    with _TAG_CACHE_LOCK:
        if repo_url in _TAG_CACHE:
            return _TAG_CACHE[repo_url]

    refs = _fetch_tag_refs_for_repo(repo_url, timeout=timeout)
    tags = None if refs is None else [tag for _, tag in refs]
    with _TAG_CACHE_LOCK:
        _TAG_CACHE[repo_url] = tags
    return tags


def _fetch_tag_refs_for_repo(
    repo_url: str, *, timeout: int = _LS_REMOTE_TIMEOUT
) -> list[tuple[str, str]] | None:
    """Return ``(commit_sha, tag)`` pairs for *repo_url*.

    The tag names-only cache is sufficient for semver revs, but SHA refresh
    needs the commit associated with each tag to preserve the pin format.
    """
    with _TAG_CACHE_LOCK:
        if repo_url in _TAG_REF_CACHE:
            return _TAG_REF_CACHE[repo_url]

        try:
            proc = subprocess.run(
                ["git", "ls-remote", "--tags", "--refs", repo_url],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            _TAG_REF_CACHE[repo_url] = None
            _TAG_CACHE[repo_url] = None
            return None

        if proc.returncode != 0:
            _TAG_REF_CACHE[repo_url] = None
            _TAG_CACHE[repo_url] = None
            return None

        refs: list[tuple[str, str]] = []
        for line in proc.stdout.splitlines():
            # Format: "<sha>\trefs/tags/<tag>"
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[1].startswith("refs/tags/"):
                sha = parts[0].strip()
                tag = parts[1][len("refs/tags/") :]
                if sha and tag:
                    refs.append((sha, tag))

        _TAG_REF_CACHE[repo_url] = refs
        _TAG_CACHE[repo_url] = [tag for _, tag in refs]
        return refs


def _clear_tag_cache() -> None:
    """Clear the in-process tag cache (used in tests)."""
    with _TAG_CACHE_LOCK:
        _TAG_CACHE.clear()
        _TAG_REF_CACHE.clear()


def _resolve_sha_refresh(
    current_sha: str, refs: list[tuple[str, str]]
) -> tuple[str, str, str] | None:
    """Resolve a SHA pin to the newest digest within its tagged major.

    The tags attached to the pinned commit establish the hook's version intent.
    The target keeps that tag's prefix style and major, even when
    ``REPO_OPS_PRECOMMIT_UPDATE_LATEST`` is enabled.
    """
    normalized = current_sha.lower()
    matching_tags: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        candidate_sha = tag_sha.lower()
        if not (
            candidate_sha == normalized
            or (len(normalized) < 40 and candidate_sha.startswith(normalized))
        ):
            continue
        if not _is_semver_like(tag):
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r during pre-commit SHA refresh",
                tag,
            )
            continue
        matching_tags.append((tag_version, tag, tag_sha))

    if not matching_tags:
        return None

    _, current_tag, _ = max(matching_tags, key=lambda item: (item[0], item[1]))
    current_version = semver_tuple(current_tag.lstrip("v"))
    want_v = _has_v_prefix(current_tag)
    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        if not _is_semver_like(tag) or _has_v_prefix(tag) != want_v:
            continue
        try:
            tag_version = semver_tuple(tag.lstrip("v"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r during pre-commit SHA refresh",
                tag,
            )
            continue
        if tag_version and tag_version[0] == current_version[0]:
            candidates.append((tag_version, tag, tag_sha))

    if not candidates:
        return None
    _, latest_tag, latest_sha = max(candidates, key=lambda item: (item[0], item[1]))
    latest_sha_lower = latest_sha.lower()
    if latest_sha_lower == normalized or (
        len(normalized) < 40 and latest_sha_lower.startswith(normalized)
    ):
        return None
    return latest_sha, current_tag, latest_tag


def _resolve_hold_tag_target(
    current_rev: str, tags: list[str], rule: HoldRule
) -> str | None:
    """Return the highest tag at or below *rule* when *current_rev* violates it."""
    return holds.resolve_hold_tag_target(current_rev, tags, rule, context="pre-commit")


def _matching_sha_tags(
    current_sha: str, refs: list[tuple[str, str]]
) -> list[tuple[tuple[int, ...], str, str]]:
    """Return semver tags attached to *current_sha*."""
    return holds.matching_sha_tags(current_sha, refs, context="pre-commit")


def _resolve_hold_sha_target(
    current_sha: str, refs: list[tuple[str, str]], rule: HoldRule
) -> str | None:
    """Return the SHA for the highest held tag when *current_sha* violates it."""
    resolved = holds.resolve_hold_sha_target(
        current_sha, refs, rule, context="pre-commit"
    )
    return None if resolved is None else resolved[1]


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------


def check_precommit(ws: Workspace) -> list[Change]:
    """Read-only check: parse ``rev:`` pins and report outdated entries.

    Returns a list of Change objects.  Non-actionable pins (SHA, branch, network
    errors) use a ``to`` value prefixed with ``[report-only]`` so the report
    layer can distinguish them.

    Environment variables:
    - ``REPO_OPS_PRECOMMIT_UPDATE_LATEST=1``: allow cross-major bumps.
    - ``REPO_OPS_PRECOMMIT_REFRESH_SHA=1``: refresh SHA pins within their
      resolved tagged major.
    """
    allow_cross_major = _precommit_update_latest_enabled(ws)
    refresh_sha = _precommit_refresh_sha_enabled(ws)
    cfg_path = _find_precommit_config(ws.path)
    if cfg_path is None:
        return []

    try:
        text = cfg_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    repos = _parse_precommit_repos(text)
    changes: list[Change] = []
    seen_repos: set[str] = set()

    for entry in repos:
        repo_url: str = entry["repo"]
        rev: str = entry["rev"]
        label = _repo_short_name(repo_url)

        if repo_url in seen_repos:
            continue
        seen_repos.add(repo_url)

        # SHA-pinned: report-only unless the tag history can establish intent.
        if _is_sha_rev(rev):
            hold = _resolve_precommit_hold(ws, label)
            refs: list[tuple[str, str]] | None = None
            if hold is not None:
                refs = _fetch_tag_refs_for_repo(repo_url)
                if refs is not None:
                    hold_target = _resolve_hold_sha_target(rev, refs, hold)
                    if hold_target is not None:
                        changes.append(Change(name=label, from_ver=rev, to=hold_target))
                        continue
            if refresh_sha:
                refs = refs if refs is not None else _fetch_tag_refs_for_repo(repo_url)
                resolved = _resolve_sha_refresh(rev, refs or [])
                if resolved is not None:
                    latest_sha, _current_tag, latest_tag = resolved
                    if not _target_within_hold(ws, label, latest_tag):
                        continue
                    changes.append(Change(name=label, from_ver=rev, to=latest_sha))
                    continue
            changes.append(
                Change(
                    name=label,
                    from_ver=rev,
                    to=(f"{_REPORT_ONLY_PREFIX}SHA pin; consider switching to a tag"),
                )
            )
            continue

        # Non-semver tag (branch names like "main", "master", misc strings):
        if not _is_semver_like(rev):
            changes.append(
                Change(
                    name=label,
                    from_ver=rev,
                    to=f"{_REPORT_ONLY_PREFIX}non-semver rev; manual review required",
                )
            )
            continue

        # Fetch available tags
        tags = _fetch_tags_for_repo(repo_url)
        if tags is None:
            changes.append(
                Change(
                    name=label,
                    from_ver=rev,
                    to=f"{_REPORT_ONLY_PREFIX}tag fetch failed (network or git error)",
                )
            )
            continue

        hold = _resolve_precommit_hold(ws, label)
        if hold is not None:
            hold_target = _resolve_hold_tag_target(rev, tags, hold)
            if hold_target is not None:
                changes.append(Change(name=label, from_ver=rev, to=hold_target))
                continue

        latest = _resolve_latest_precommit_rev(
            rev, tags, allow_cross_major=allow_cross_major
        )
        if latest is None:
            continue  # already at latest or indeterminate

        if not _target_within_hold(ws, label, latest):
            continue
        changes.append(Change(name=label, from_ver=rev, to=latest))

    return changes


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _enforce_version_ceilings(
    ws: Workspace, repos: list[dict[str, Any]]
) -> dict[int, str]:
    """Return line replacements that clamp held pre-commit revs."""
    line_bumps: dict[int, str] = {}
    for entry in repos:
        repo_url: str = entry["repo"]
        rev: str = entry["rev"]
        line_no: int = entry["rev_line_no"]
        package = _repo_short_name(repo_url)
        rule = _resolve_precommit_hold(ws, package)
        if rule is None:
            continue

        if _is_sha_rev(rev):
            refs = _fetch_tag_refs_for_repo(repo_url)
            if refs is None:
                raise RuntimeError(
                    f"hold: failed to clamp {package} back to <={rule.max_version} "
                    "(pre-commit): tag fetch failed while resolving SHA pin"
                )
            matching_tags = _matching_sha_tags(rev, refs)
            if not matching_tags:
                continue
            _, current_tag, _ = max(matching_tags, key=lambda item: (item[0], item[1]))
            if version_satisfies_ceiling(current_tag.lstrip("v"), rule.max_version):
                continue
            target = _resolve_hold_sha_target(rev, refs, rule)
            if target is None:
                raise RuntimeError(
                    f"hold: failed to clamp {package} back to <={rule.max_version} "
                    "(pre-commit): no available tag satisfies ceiling"
                )
        elif _is_semver_like(rev):
            if version_satisfies_ceiling(rev.lstrip("v"), rule.max_version):
                continue
            tags = _fetch_tags_for_repo(repo_url)
            if tags is None:
                raise RuntimeError(
                    f"hold: failed to clamp {package} back to <={rule.max_version} "
                    "(pre-commit): tag fetch failed while resolving tag rev"
                )
            target = _resolve_hold_tag_target(rev, tags, rule)
            if target is None:
                raise RuntimeError(
                    f"hold: failed to clamp {package} back to <={rule.max_version} "
                    "(pre-commit): no available tag satisfies ceiling"
                )
        else:
            continue

        line_bumps[line_no] = target
    return line_bumps


def apply_precommit(ws: Workspace) -> None:
    """Apply rev: bumps in-place (byte-preserving single-line edits).

    Runs check_precommit to discover actionable bumps, then rewrites only the
    value portion of the matching ``rev:`` line in the config file.  Skips
    report-only entries (SHA pins, non-semver, network failures).

    Correctness notes:
    - Replacement is keyed by exact line number (from re-parsing the config),
      not by the rev value string.  Two repos pinned at the same current rev
      each have their own line-number key, so there is no dict collision and
      each repo gets its own independent target rev (Bug 1 fix).
    - Config is read as raw bytes and decoded via latin-1, which maps each byte
      0-255 to exactly one code point and back — so non-UTF-8 bytes round-trip
      unchanged regardless of what encoding the file actually uses (Bug 3 fix).
    """
    cfg_path = _find_precommit_config(ws.path)
    if cfg_path is None:
        return

    # Discover regular bumps first; this also warms the tag caches.
    check_precommit(ws)

    # Bug 3 fix: read as bytes, decode via latin-1 (lossless — byte N decodes to
    # U+00NN and re-encodes back to byte N, so non-UTF-8 content is never
    # replaced with U+FFFD or otherwise corrupted on writeback).
    try:
        raw = cfg_path.read_bytes()
    except OSError:
        return
    text = raw.decode("latin-1")

    # Bug 1 fix: re-parse config to get per-entry (repo_url, rev, rev_line_no).
    # Build a line-number → new_rev map so two repos at the same current rev
    # each get an independent key and their own target rev.
    # Tag cache is already warm from check_precommit above, so these calls are
    # instant cache hits.
    allow_cross_major = _precommit_update_latest_enabled(ws)
    refresh_sha = _precommit_refresh_sha_enabled(ws)
    repos = _parse_precommit_repos(text)

    line_bumps: dict[int, str] = {}  # 1-indexed rev_line_no → target rev
    for entry in repos:
        repo_url: str = entry["repo"]
        from_ver: str = entry["rev"]
        rev_line_no: int = entry["rev_line_no"]

        if _is_sha_rev(from_ver):
            if refresh_sha:
                refs = _fetch_tag_refs_for_repo(repo_url)
                resolved = _resolve_sha_refresh(from_ver, refs or [])
                if resolved is not None:
                    new_sha, _current_tag, latest_tag = resolved
                    if not _target_within_hold(
                        ws, _repo_short_name(repo_url), latest_tag
                    ):
                        continue
                    line_bumps[rev_line_no] = new_sha
            continue

        if not _is_semver_like(from_ver):
            continue

        tags = _fetch_tags_for_repo(repo_url)
        if tags is None:
            continue

        new_rev = _resolve_latest_precommit_rev(
            from_ver, tags, allow_cross_major=allow_cross_major
        )
        if new_rev is not None and _target_within_hold(
            ws, _repo_short_name(repo_url), new_rev
        ):
            line_bumps[rev_line_no] = new_rev

    line_bumps.update(_enforce_version_ceilings(ws, repos))
    if not line_bumps:
        return

    # CRLF-safe: use splitlines(keepends=True) so original line endings survive.
    lines = text.splitlines(keepends=True)
    changed = False

    for i, line in enumerate(lines):
        line_no = i + 1  # rev_line_no is 1-indexed
        new_rev = line_bumps.get(line_no)
        if new_rev is None:
            continue
        m = _REV_LINE_RE.match(line)
        if not m:
            continue  # shouldn't happen; parsed line numbers come from this text
        indent = m.group(1)
        # Determine quote style from the original value
        raw_val = m.group(2).strip()
        if raw_val.startswith(("'", '"')):
            quote = raw_val[0]
            new_line = f"{indent}rev: {quote}{new_rev}{quote}\n"
        else:
            new_line = f"{indent}rev: {new_rev}\n"
        # Preserve original line ending (CRLF or LF)
        original_ending = line[len(line.rstrip("\r\n")) :]
        new_line = new_line.rstrip("\n") + original_ending
        lines[i] = new_line
        changed = True

    if changed:
        # Bug 3 fix: encode as latin-1 to exactly reverse the decode.
        cfg_path.write_bytes("".join(lines).encode("latin-1"))
