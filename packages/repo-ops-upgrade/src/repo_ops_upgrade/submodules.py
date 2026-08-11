"""Git submodule discovery, ordering, post-apply pointer hints, and tag-advance detection."""

from __future__ import annotations

import configparser
import contextlib
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.git import git_repo_for, is_git_dirty, run_git
from repo_ops_core.version_utils import semver_tuple

logger = logging.getLogger(__name__)

SubmoduleOrder = str  # "discovery" | "children-first"


@dataclass(frozen=True)
class SubmoduleLink:
    """One submodule entry under a parent git repo."""

    path: str
    git_repo: Path
    parent: Path
    url: str = field(default="")  # remote URL from .gitmodules; "" when unknown

    def to_json(self) -> dict[str, str]:
        return {
            "path": self.path,
            "git_repo": str(self.git_repo.resolve()),
            "parent": str(self.parent.resolve()),
            "url": self.url,
        }


def _find_gitmodules(root: Path, *, max_depth: int = 32) -> list[Path]:
    """Walk for .gitmodules with depth limit and symlink-cycle guard."""
    found: list[Path] = []
    seen_roots: set[str] = set()
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            real = os.path.realpath(current)
        except OSError:
            continue
        if real in seen_roots:
            continue
        seen_roots.add(real)
        if depth > max_depth:
            continue
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name == ".gitmodules" and entry.is_file():
                found.append(entry)
            elif entry.is_dir() and not entry.is_symlink():
                # Prune well-known noise dirs (node_modules, target, .venv, …)
                # to match iter_scanned_dirs behaviour and avoid registering a
                # .gitmodules found inside a vendored or generated subtree.
                # (closes #43)
                if entry.name in SKIP_DIRS:
                    continue
                stack.append((entry, depth + 1))
    return sorted(found)


def _parse_gitmodules_file(gitmodules_path: Path) -> list[tuple[str, str]]:
    """Return (path, url) pairs from a .gitmodules file."""
    parser = configparser.ConfigParser()
    parser.read(gitmodules_path, encoding="utf-8")
    entries: list[tuple[str, str]] = []
    for section in parser.sections():
        if not section.startswith("submodule "):
            continue
        path = (parser.get(section, "path", fallback="") or "").strip()
        url = (parser.get(section, "url", fallback="") or "").strip()
        if path:
            entries.append((path, url))
    return entries


def discover_submodules_under_roots(
    roots: list[str], *, flat: bool
) -> list[SubmoduleLink]:
    """Find .gitmodules under scanned roots and resolve parent/child git repos."""
    links: list[SubmoduleLink] = []
    seen: set[tuple[str, str, str]] = set()

    for root_s in roots:
        root = Path(root_s).expanduser().resolve()
        if not root.is_dir():
            continue
        candidates: list[Path]
        if flat and len(roots) == 1:
            gm = root / ".gitmodules"
            candidates = [gm] if gm.is_file() else []
        else:
            candidates = _find_gitmodules(root)

        for gitmodules_path in candidates:
            parent = git_repo_for(gitmodules_path.parent)
            if parent is None:
                continue
            parent = parent.resolve()
            for rel_path, url in _parse_gitmodules_file(gitmodules_path):
                child_dir = (parent / rel_path).resolve()
                child_repo = git_repo_for(child_dir)
                # Skip uninitialized submodules (.git absent → git_repo_for
                # walks up to the parent repo) and self-referential links.
                # The second guard catches a path="." entry: child_dir resolves
                # to the parent itself, git_repo_for returns the parent repo,
                # so child_repo.resolve() == child_dir passes the first check
                # but would produce SubmoduleLink(git_repo=parent, parent=parent).
                # (closes #47)
                if child_repo is None or child_repo.resolve() != child_dir:
                    continue
                if child_repo.resolve() == parent:
                    continue
                key = (str(parent), rel_path, str(child_repo.resolve()))
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    SubmoduleLink(
                        path=rel_path.replace("\\", "/"),
                        git_repo=child_repo.resolve(),
                        parent=parent,
                        url=url,
                    )
                )
    return links


def submodules_to_json(links: list[SubmoduleLink]) -> list[dict[str, str]]:
    return [link.to_json() for link in links]


def sort_bundles_children_first(
    bundle_keys: list[Path],
    links: list[SubmoduleLink],
) -> list[Path]:
    """Topological order: child git repos before parents that embed them."""
    if not links:
        return bundle_keys

    key_set = set(bundle_keys)
    depth: dict[Path, int] = dict.fromkeys(bundle_keys, 0)

    # Cap iterations to len(bundle_keys): a valid DAG with N nodes converges
    # in at most N-1 passes; a cycle (or self-referential link) cannot spin
    # forever because we break once the budget is exhausted.
    budget = len(bundle_keys)
    changed = True
    while changed and budget > 0:
        budget -= 1
        changed = False
        for link in links:
            if link.git_repo not in key_set or link.parent not in key_set:
                continue
            parent_depth = depth[link.git_repo] + 1
            if parent_depth > depth[link.parent]:
                depth[link.parent] = parent_depth
                changed = True

    return sorted(bundle_keys, key=lambda k: (depth.get(k, 0), str(k)))


def parent_repos_needing_pointer_commit(
    links: list[SubmoduleLink],
) -> list[dict[str, str]]:
    """Parents whose submodule gitlinks differ between index and HEAD/worktree."""
    hints: list[dict[str, str]] = []
    parents: dict[Path, list[str]] = {}
    for link in links:
        parents.setdefault(link.parent, []).append(link.path)

    for parent, paths in sorted(parents.items(), key=lambda item: str(item[0])):
        dirty_paths = _dirty_submodule_paths(parent, paths)
        if not dirty_paths:
            continue
        hints.append(
            {
                "parent": str(parent),
                "paths": ",".join(dirty_paths),
                "hint": (
                    "Submodule pointer(s) changed — commit parent after child "
                    "repos are pushed (git-push-order: children first)."
                ),
            }
        )
    return hints


def _dirty_submodule_paths(parent: Path, submodule_paths: list[str]) -> list[str]:
    proc = run_git(parent, "status", "--porcelain", timeout=30)
    if proc.returncode != 0:
        return []
    dirty: list[str] = []
    path_set = {p.replace("\\", "/") for p in submodule_paths}
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        # Submodule gitlink changes often appear as " M path" or "M  path"
        if entry in path_set or any(
            entry == p or entry.endswith(f"/{p}") for p in path_set
        ):
            dirty.append(entry)
            continue
        for p in path_set:
            if entry == p or entry.startswith(f"{p}/"):
                dirty.append(p)
                break
    # Also catch '+' from `git submodule status` (checkout ahead of index)
    sub_proc = run_git(parent, "submodule", "status", timeout=60)
    if sub_proc.returncode == 0:
        for line in sub_proc.stdout.splitlines():
            if not line or line[0] not in "+U":
                continue
            rest = line[1:].strip()
            parts = rest.split()
            if len(parts) < 2:
                continue
            sub_path = parts[1].replace("\\", "/")
            if sub_path in path_set and sub_path not in dirty:
                dirty.append(sub_path)
    return sorted(set(dirty))


# ---------------------------------------------------------------------------
# Submodule tag-advance detection (plan) and application (apply)
# ---------------------------------------------------------------------------

#: Prefix that marks non-actionable entries (mirrors precommit and container
#: ecosystems so the report layer can distinguish them uniformly).
_REPORT_ONLY_PREFIX = "[report-only] "

#: Environment variable opt-in for cross-major tag advances.
_SUBMODULE_UPDATE_LATEST_ENV = "REPO_OPS_SUBMODULE_UPDATE_LATEST"

#: Environment variable opt-in for refreshing SHA-only submodule pointers.
_SUBMODULE_REFRESH_SHA_ENV = "REPO_OPS_SUBMODULE_REFRESH_SHA"

#: Bounded timeout (seconds) for ``git ls-remote`` calls.
_LS_REMOTE_TIMEOUT = 30

#: Recognises semver-like tags with or without a leading ``v`` and an optional
#: pre-release / build-metadata suffix (e.g. ``v1.2.3``, ``1.0.0-rc1``).
_SEMVER_TAG_RE = re.compile(r"^v?(\d+)(?:\.\d+)*(?:[-.][\w.]+)?$")

#: In-process cache: URL → tag list (or None on error).
#: One entry per unique upstream URL so repeated checks cost only one RTT.
_SUBMODULE_TAG_CACHE: dict[str, list[str] | None] = {}
_SUBMODULE_TAG_REF_CACHE: dict[str, list[tuple[str, str]] | None] = {}


@dataclass(frozen=True)
class SubmoduleTagBump:
    """Potential tag advance for one submodule.

    ``latest_tag`` is either a concrete semver tag (actionable) or a
    ``[report-only] …`` message (informational) — the same convention used by
    the precommit and container ecosystems.
    """

    path: str  # relative path from parent repo
    url: str  # upstream remote URL from .gitmodules
    current_tag: str  # tag at HEAD, or "" when HEAD has no exact tag
    latest_tag: str  # target tag, or "[report-only] …" when non-actionable
    parent: Path  # parent git repo root
    git_repo: Path  # resolved absolute path to the submodule checkout

    @property
    def is_actionable(self) -> bool:
        """True when ``latest_tag`` is a concrete target (not report-only)."""
        return not self.latest_tag.startswith(_REPORT_ONLY_PREFIX)

    def to_json(self) -> dict[str, str]:
        return {
            "path": self.path,
            "url": self.url,
            "current_tag": self.current_tag,
            "latest_tag": self.latest_tag,
            "parent": str(self.parent),
            "git_repo": str(self.git_repo),
            "actionable": str(self.is_actionable).lower(),
        }


def _is_semver_tag(tag: str) -> bool:
    """True when *tag* looks like a semver version (bare or ``v``-prefixed)."""
    return bool(_SEMVER_TAG_RE.match(tag))


def _has_v_prefix(tag: str) -> bool:
    return tag.startswith(("v", "V"))


def _fetch_remote_tags(
    url: str, *, timeout: int = _LS_REMOTE_TIMEOUT
) -> list[str] | None:
    """Return annotated tag names for *url* via ``git ls-remote --tags --refs``.

    Results are cached in-process so repeated checks for the same URL cost only
    one subprocess call.  Returns ``None`` on any error so callers can degrade
    to report-only without raising.
    """
    if url in _SUBMODULE_TAG_CACHE:
        return _SUBMODULE_TAG_CACHE[url]
    refs = _fetch_remote_tag_refs(url, timeout=timeout)
    tags = None if refs is None else [tag for _, tag in refs]
    _SUBMODULE_TAG_CACHE[url] = tags
    return tags


def _fetch_remote_tag_refs(
    url: str, *, timeout: int = _LS_REMOTE_TIMEOUT
) -> list[tuple[str, str]] | None:
    """Return ``(commit_sha, tag)`` pairs for a submodule URL."""
    if url in _SUBMODULE_TAG_REF_CACHE:
        return _SUBMODULE_TAG_REF_CACHE[url]

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", "--", url],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_ALLOW_PROTOCOL": "https:ssh"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _SUBMODULE_TAG_REF_CACHE[url] = None
        _SUBMODULE_TAG_CACHE[url] = None
        return None

    if proc.returncode != 0:
        _SUBMODULE_TAG_REF_CACHE[url] = None
        _SUBMODULE_TAG_CACHE[url] = None
        return None

    refs: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].startswith("refs/tags/"):
            sha = parts[0].strip()
            tag = parts[1][len("refs/tags/") :]
            if sha and tag:
                refs.append((sha, tag))

    _SUBMODULE_TAG_REF_CACHE[url] = refs
    _SUBMODULE_TAG_CACHE[url] = [tag for _, tag in refs]
    return refs


def _clear_submodule_tag_cache() -> None:
    """Clear the in-process tag cache (for use in tests)."""
    _SUBMODULE_TAG_CACHE.clear()
    _SUBMODULE_TAG_REF_CACHE.clear()


def _current_submodule_tag(git_repo: Path) -> str | None:
    """Return the exact tag that HEAD points to in *git_repo*, or ``None``.

    Uses ``git describe --tags --exact-match`` which succeeds only when HEAD is
    directly tagged.  Returns ``None`` for SHA-only pins and untagged HEADs.
    """
    try:
        proc = run_git(git_repo, "describe", "--tags", "--exact-match", "HEAD")
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    tag = proc.stdout.strip()
    return tag or None


def _current_submodule_sha(git_repo: Path) -> str | None:
    """Return the full commit SHA currently checked out in *git_repo*."""
    try:
        proc = run_git(git_repo, "rev-parse", "HEAD")
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _resolve_latest_submodule_tag(
    current_tag: str,
    available_tags: list[str],
    *,
    allow_cross_major: bool = False,
) -> str | None:
    """Return the best candidate tag for *current_tag* from *available_tags*.

    Matching rules:
    - *current_tag* must be semver-like; returns ``None`` otherwise.
    - Candidates must share the same ``v``-prefix style as *current_tag*.
    - Conservative (default): same major only.
    - ``allow_cross_major=True``: pick the global best across all majors.
    - Returns ``None`` when already at latest or no suitable candidate exists.
    """
    if not _is_semver_tag(current_tag):
        return None

    want_v = _has_v_prefix(current_tag)
    bare = current_tag.lstrip("vV")
    try:
        current_tup = semver_tuple(bare)
    except Exception:
        return None

    candidates: list[tuple[tuple[int, ...], str]] = []
    for tag in available_tags:
        if not _is_semver_tag(tag):
            continue
        if _has_v_prefix(tag) != want_v:
            continue
        tag_bare = tag.lstrip("vV")
        try:
            tup = semver_tuple(tag_bare)
        except Exception:  # noqa: S112
            continue
        candidates.append((tup, tag))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)

    if not allow_cross_major:
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


def _resolve_sha_refresh(
    current_sha: str, refs: list[tuple[str, str]]
) -> tuple[str, str] | None:
    """Return the newest tag within the major and style resolved from a SHA."""
    normalized = current_sha.lower()
    matching: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        candidate_sha = tag_sha.lower()
        if not (
            candidate_sha == normalized
            or (len(normalized) < 40 and candidate_sha.startswith(normalized))
        ):
            continue
        if not _is_semver_tag(tag):
            continue
        try:
            version = semver_tuple(tag.lstrip("vV"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r during submodule SHA refresh",
                tag,
            )
            continue
        matching.append((version, tag, tag_sha))

    if not matching:
        return None
    _, current_tag, _ = max(matching, key=lambda item: (item[0], item[1]))
    current_version = semver_tuple(current_tag.lstrip("vV"))
    want_v = _has_v_prefix(current_tag)
    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for tag_sha, tag in refs:
        if not _is_semver_tag(tag) or _has_v_prefix(tag) != want_v:
            continue
        try:
            version = semver_tuple(tag.lstrip("vV"))
        except Exception:
            logger.warning(
                "ignoring invalid semver tag %r during submodule SHA refresh",
                tag,
            )
            continue
        if version and version[0] == current_version[0]:
            candidates.append((version, tag, tag_sha))
    if not candidates:
        return None
    _, latest_tag, latest_sha = max(candidates, key=lambda item: (item[0], item[1]))
    latest_sha_lower = latest_sha.lower()
    if latest_sha_lower == normalized or (
        len(normalized) < 40 and latest_sha_lower.startswith(normalized)
    ):
        return None
    return current_tag, latest_tag


def check_submodule_tag_advances(
    links: list[SubmoduleLink],
) -> list[SubmoduleTagBump]:
    """Read-only check: detect available upstream semver tag advances.

    For each submodule in *links*:

    - Determines the current tag at HEAD; report-only when the HEAD has no
      exact tag (SHA-only pin).
    - Skips submodules with no URL (cannot resolve upstream).
    - Queries upstream tags via ``git ls-remote`` with a 30 s timeout; network
      failures degrade to report-only so the check never raises.
    - Non-semver current tags are report-only.
    - Submodules already at the latest semver tag within the same major produce
      no entry.

    Set ``REPO_OPS_SUBMODULE_UPDATE_LATEST=1`` to allow cross-major advances.
    Set ``REPO_OPS_SUBMODULE_REFRESH_SHA=1`` to refresh SHA-only HEADs when
    their upstream tag history establishes a same-major target.
    """
    allow_cross_major = os.environ.get(_SUBMODULE_UPDATE_LATEST_ENV, "") == "1"
    refresh_sha = os.environ.get(_SUBMODULE_REFRESH_SHA_ENV, "") == "1"
    bumps: list[SubmoduleTagBump] = []

    for link in links:
        url = link.url
        if not url:
            # No URL recorded — cannot resolve upstream; skip silently.
            continue

        current_tag = _current_submodule_tag(link.git_repo)

        if current_tag is None:
            if refresh_sha:
                current_sha = _current_submodule_sha(link.git_repo)
                refs = _fetch_remote_tag_refs(url) if current_sha else None
                resolved = _resolve_sha_refresh(current_sha or "", refs or [])
                if resolved is not None:
                    _resolved_current_tag, latest_tag = resolved
                    bumps.append(
                        SubmoduleTagBump(
                            path=link.path,
                            url=url,
                            current_tag="",
                            latest_tag=latest_tag,
                            parent=link.parent,
                            git_repo=link.git_repo,
                        )
                    )
                    continue
            bumps.append(
                SubmoduleTagBump(
                    path=link.path,
                    url=url,
                    current_tag="",
                    latest_tag=(
                        f"{_REPORT_ONLY_PREFIX}no exact tag at HEAD; "
                        "pin to a semver tag to enable updates"
                    ),
                    parent=link.parent,
                    git_repo=link.git_repo,
                )
            )
            continue

        if not _is_semver_tag(current_tag):
            bumps.append(
                SubmoduleTagBump(
                    path=link.path,
                    url=url,
                    current_tag=current_tag,
                    latest_tag=f"{_REPORT_ONLY_PREFIX}non-semver tag; manual review required",
                    parent=link.parent,
                    git_repo=link.git_repo,
                )
            )
            continue

        tags = _fetch_remote_tags(url)
        if tags is None:
            bumps.append(
                SubmoduleTagBump(
                    path=link.path,
                    url=url,
                    current_tag=current_tag,
                    latest_tag=f"{_REPORT_ONLY_PREFIX}tag fetch failed (network or git error)",
                    parent=link.parent,
                    git_repo=link.git_repo,
                )
            )
            continue

        latest = _resolve_latest_submodule_tag(
            current_tag, tags, allow_cross_major=allow_cross_major
        )
        if latest is None:
            continue  # already at latest or indeterminate

        bumps.append(
            SubmoduleTagBump(
                path=link.path,
                url=url,
                current_tag=current_tag,
                latest_tag=latest,
                parent=link.parent,
                git_repo=link.git_repo,
            )
        )

    return bumps


def apply_submodule_tag_advances(
    links: list[SubmoduleLink],
    *,
    allow_dirty: bool = False,
) -> list[SubmoduleTagBump]:
    """Advance submodule pointers to the latest upstream semver tag.

    Calls ``check_submodule_tag_advances`` to discover actionable bumps, then
    for each actionable bump:

    1. Checks the submodule checkout is clean (skips if dirty and
       ``allow_dirty=False``, mirroring orchestration.py's guard).
    2. Fetches the target tag into the submodule clone.
    3. Checks out the new tag with ``--detach refs/tags/<tag>`` to avoid
       ambiguity when a branch name shadows a tag name.
    4. Stages the updated gitlink pointer in the parent repo.

    Non-actionable bumps (SHA-only, non-semver, network errors) are skipped.
    Subprocess failures for individual submodules are caught so one broken
    submodule cannot abort advances for the rest.

    Returns the full list of bumps from ``check_submodule_tag_advances``
    (both applied and report-only), mirroring the precommit/container pattern
    so the caller can report all findings in one pass.
    """
    bumps = check_submodule_tag_advances(links)

    for bump in bumps:
        if not bump.is_actionable:
            continue
        submod_dir = bump.git_repo

        # Dirty-tree guard: skip if the submodule checkout has uncommitted
        # changes — checking out a new tag over a dirty tree risks data loss.
        # Mirrors the allow_dirty guard in orchestration.py.
        if not allow_dirty and is_git_dirty(submod_dir):
            continue

        try:
            # Fetch only the specific target tag to keep network overhead low.
            fetch_proc = run_git(
                submod_dir,
                "fetch",
                "--",
                bump.url,
                f"refs/tags/{bump.latest_tag}:refs/tags/{bump.latest_tag}",
                env={**os.environ, "GIT_ALLOW_PROTOCOL": "https:ssh"},
                timeout=120,
            )
        except (FileNotFoundError, OSError):
            continue
        if fetch_proc.returncode != 0:
            continue

        try:
            # Use --detach with the full refs/tags/<tag> ref to avoid ambiguity
            # when a branch name shadows a tag name (ENH #18).
            checkout_proc = run_git(
                submod_dir,
                "checkout",
                "--detach",
                f"refs/tags/{bump.latest_tag}",
                timeout=30,
            )
        except (FileNotFoundError, OSError):
            continue
        if checkout_proc.returncode != 0:
            continue

        # Stage the updated gitlink in the parent.  A failure here is unlikely
        # (checkout succeeded) but non-fatal — the caller can commit manually.
        with contextlib.suppress(FileNotFoundError, OSError):
            run_git(bump.parent, "add", bump.path, timeout=30)

    return bumps
