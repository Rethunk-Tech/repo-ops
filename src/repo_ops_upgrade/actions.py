"""GitHub Actions workflow pin audit and apply."""

from __future__ import annotations

import contextlib
import fnmatch
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from repo_ops_core.exclude import workspace_matches_exclude
from repo_ops_core.filesystem import write_json_atomic
from repo_ops_core.git import github_owner_repo, github_remote
from repo_ops_core.holds import HoldRule, resolve_package_hold, target_within_hold
from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple, version_satisfies_ceiling
from repo_ops_models import ActionBump
from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    WorkflowParseError,
    parse_workflow_uses,
    update_workflow_pins,
)

logger = logging.getLogger(__name__)

_TAG_CACHE: dict[str, dict | None] = {}
# Action tags move on release cadence, not on sweep cadence, so a few hours of
# reuse costs at most one sweep's delay on a bump and saves a `gh api` round
# trip per action on every run.
_TAG_CACHE_MAX_AGE_S = 6 * 60 * 60


def _tag_cache_path() -> Path:
    """Return ``$XDG_CACHE_HOME/repo-ops/action-tags.json``."""
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "repo-ops" / "action-tags.json"


def _tag_from_disk(raw: dict) -> dict | None:
    """Rebuild the in-memory shape (sets) from the JSON-safe stored shape."""
    if raw.get("miss"):
        return None
    return {
        "bare_majors": set(raw.get("bare_majors") or []),
        "max_major": raw.get("max_major"),
        "all_tags": set(raw.get("all_tags") or []),
        "sha_to_tags": raw.get("sha_to_tags") or {},
        "truncated": bool(raw.get("truncated")),
    }


def _tag_to_disk(info: dict | None) -> dict:
    if info is None:
        return {"miss": True, "ts": time.time()}
    return {
        "bare_majors": sorted(info["bare_majors"]),
        "max_major": info["max_major"],
        "all_tags": sorted(info["all_tags"]),
        "sha_to_tags": info["sha_to_tags"],
        "truncated": info.get("truncated", False),
        "ts": time.time(),
    }


def load_tag_cache_into(store: dict[str, dict | None]) -> None:
    """Seed *store* from disk, dropping entries older than the max age."""
    path = _tag_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    now = time.time()
    for repo, raw in data.items():
        if isinstance(raw, dict) and now - raw.get("ts", 0) <= _TAG_CACHE_MAX_AGE_S:
            store[repo] = _tag_from_disk(raw)


def save_tag_cache_from(store: dict[str, dict | None]) -> None:
    """Persist *store*, merged over unexpired entries already on disk."""
    if not store:
        return
    path = _tag_cache_path()
    merged: dict[str, dict] = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        now = time.time()
        if isinstance(existing, dict):
            merged = {
                k: v
                for k, v in existing.items()
                if isinstance(v, dict) and now - v.get("ts", 0) <= _TAG_CACHE_MAX_AGE_S
            }
    except (OSError, ValueError):
        pass
    for repo, info in store.items():
        merged[repo] = _tag_to_disk(info)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, merged)


# ---------------------------------------------------------------------------
# Org allowed-actions policy cache
# ---------------------------------------------------------------------------

# In-process cache: owner → policy dict (or None when not applicable/accessible).
# Shape when populated:
#   {
#     "enabled": bool,
#     "allowed_actions": "all" | "local_only" | "selected",
#     # Only present when allowed_actions == "selected":
#     "github_owned_allowed": bool,
#     "verified_allowed": bool,
#     "patterns_allowed": list[str],
#   }
_ORG_POLICY_CACHE: dict[str, dict | None] = {}

_MAJOR_TAG_RE = re.compile(r"^v(\d+)(?:\.|$)")
# A release tag names at least major.minor (Rethunk-Tech/gh-actions ships v1.10, not v1.10.0).
# A bare vN floats between releases, so it never counts as one.
_EXACT_TAG_RE = re.compile(r"v(\d+)\.\d+(?:[.+-].*)?")

# Full 40-hex SHA or short sha<hex> prefix
_SHA_RE = re.compile(r"^(?:sha[0-9a-f]+|[0-9a-f]{7,40})$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# actionlint severity classification
# ---------------------------------------------------------------------------

# Matches the shellcheck severity token inside an actionlint finding line,
# e.g. "SC2034:warning:" or "SC1009:error:".
_SC_SEVERITY_RE = re.compile(r"\bSC\d+:(error|warning|info|style):")

# Matches the leading "file:line:col: " header that every actionlint diagnostic
# line begins with, e.g. ".github/workflows/ci.yml:26:7: ".
_FINDING_HEADER_RE = re.compile(r"^.+?:\d+:\d+: ")


def _classify_actionlint_severity(output: str) -> str:
    """Classify a non-zero actionlint result as 'error' or 'warning'.

    Returns 'warning' (non-fatal) ONLY when every recognizable diagnostic is a
    shellcheck finding of warning/info/style severity.  Native actionlint
    findings, shellcheck :error: findings, and unrecognized output stay 'error'
    (fatal).
    """
    text = (output or "").strip()
    if not text:
        return "error"
    finding_lines = [ln for ln in text.splitlines() if _FINDING_HEADER_RE.match(ln)]
    if not finding_lines:
        return "error"  # unrecognized (e.g. "actionlint timed out") → fatal
    for ln in finding_lines:
        if "shellcheck reported issue" not in ln:
            return "error"  # native actionlint finding → fatal
        m = _SC_SEVERITY_RE.search(ln)
        if not m or m.group(1) == "error":
            return "error"  # shellcheck error or unparseable severity → fatal
    return "warning"


# ---------------------------------------------------------------------------
# Actionlint result cache
# ---------------------------------------------------------------------------

# Maximum age (seconds) for a cache entry before it is pruned on write.
_LINT_CACHE_MAX_AGE_S: int = 90 * 24 * 3600  # 90 days

# Maximum number of entries kept in the cache file (simple LRU-by-touch).
_LINT_CACHE_MAX_ENTRIES: int = 4096

# In-process version string cache (populated on first _run_actionlint call).
_actionlint_version_cache: str | None = None


def _derive_owner(repo: Path) -> str | None:
    """Return the GitHub org/user owning *repo*'s github.com remote, or None."""
    url = github_remote(repo)
    owner_repo = github_owner_repo(url) if url else None
    return owner_repo.split("/")[0] if owner_repo else None


def _action_policy_violation(action: str, policy: dict, org_owner: str | None = None) -> str | None:
    """Return a human-readable reason string when *action* is blocked by *policy*.

    Returns ``None`` when the action is allowed.

    Rules:
    - ``enabled=False`` → every action is blocked.
    - ``allowed_actions=all`` → nothing blocked.
    - ``allowed_actions=selected`` → allowed when:
        - ``github_owned_allowed`` and the action owner is ``actions`` or
          ``github``, OR
        - the action matches any glob in ``patterns_allowed`` (fnmatch semantics
          per GitHub docs: ``owner/*`` matches ``owner/any-repo``).
    - ``allowed_actions=local_only`` → allowed when the action's owner matches
      *org_owner* (case-insensitive); same-owner refs pass, cross-org refs are
      blocked. Needs *org_owner* to tell those apart, so callers evaluating a
      ``local_only`` policy must pass it — without it, ``local_only`` is
      treated as an unrecognized mode (permissive).
    """
    allowed_actions = policy.get("allowed_actions", "all")

    if not policy.get("enabled", True):
        return f"org policy (disabled): {action} not allowed — Actions are disabled for this org"

    if allowed_actions == "all":
        return None

    # Path-local actions (``./``) are always allowed regardless of policy mode.
    if action.startswith("./"):
        return None

    if allowed_actions == "selected":
        action_owner = action.split("/")[0] if "/" in action else ""
        github_owned = action_owner in ("actions", "github")
        if github_owned and policy.get("github_owned_allowed", False):
            return None
        patterns: list[str] = policy.get("patterns_allowed", [])
        for pat in patterns:
            if fnmatch.fnmatch(action, pat):
                return None
        return (
            f"org policy (selected): {action} not in allowlist"
            " — workflows using it will fail at startup"
        )

    if allowed_actions == "local_only" and org_owner is not None:
        action_owner = action.split("/")[0] if "/" in action else ""
        if action_owner.lower() == org_owner.lower():
            return None  # same-org action — allowed
        return (
            f"org policy (local_only): {action} not allowed"
            " — workflows using it will fail at startup"
        )

    # Unknown mode (or local_only without an org_owner) — be permissive.
    return None


def _fetch_org_policy_cached(owner: str, store: dict[str, dict | None]) -> dict | None:
    """Return cached org policy for *owner* from *store*, fetching via ``gh api`` on miss."""
    if owner in store:
        return store[owner]

    if not shutil.which("gh"):
        store[owner] = None
        return None

    try:
        proc = subprocess.run(
            ["gh", "api", f"orgs/{owner}/actions/permissions"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        store[owner] = None
        return None

    # 404 → user account; 403 → no access; either → skip silently.
    if proc.returncode != 0:
        store[owner] = None
        return None

    try:
        data: object = json.loads(proc.stdout)
    except Exception:
        store[owner] = None
        return None

    if not isinstance(data, dict):
        store[owner] = None
        return None

    enabled: bool = bool(data.get("enabled", True))
    allowed_actions: str = str(data.get("allowed_actions", "all"))

    policy: dict = {
        "enabled": enabled,
        "allowed_actions": allowed_actions,
        "github_owned_allowed": False,
        "verified_allowed": False,
        "patterns_allowed": [],
    }

    if enabled and allowed_actions == "selected":
        try:
            sel_proc = subprocess.run(
                ["gh", "api", f"orgs/{owner}/actions/permissions/selected-actions"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if sel_proc.returncode == 0:
                sel: object = json.loads(sel_proc.stdout)
                if isinstance(sel, dict):
                    policy["github_owned_allowed"] = bool(sel.get("github_owned_allowed", False))
                    policy["verified_allowed"] = bool(sel.get("verified_allowed", False))
                    patterns = sel.get("patterns_allowed")
                    if isinstance(patterns, list):
                        policy["patterns_allowed"] = [
                            str(p) for p in patterns if isinstance(p, str)
                        ]
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            # Non-fatal: an unreadable selected-actions policy leaves the
            # defaults above in place.
            logger.debug("selected-actions fetch failed for %s: %s", owner, exc)

    store[owner] = policy
    return policy


def _actionlint_cache_path() -> Path:
    """Return ``$XDG_CACHE_HOME/repo-ops/actionlint-cache.json``."""
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "repo-ops" / "actionlint-cache.json"


def _load_lint_cache(path: Path) -> dict[str, dict]:
    """Load the actionlint cache JSON.  Returns ``{}`` on any error."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_lint_cache(path: Path, cache: dict[str, dict]) -> None:
    """Atomically write *cache* to *path*, pruning old entries first."""
    now = time.time()
    # Drop entries older than the max age.
    pruned = {
        k: v
        for k, v in cache.items()
        if isinstance(v, dict) and now - v.get("ts", 0) <= _LINT_CACHE_MAX_AGE_S
    }
    # Cap total entries (drop oldest by timestamp).
    if len(pruned) > _LINT_CACHE_MAX_ENTRIES:
        sorted_keys = sorted(pruned, key=lambda k: pruned[k].get("ts", 0), reverse=True)
        pruned = {k: pruned[k] for k in sorted_keys[:_LINT_CACHE_MAX_ENTRIES]}
    try:
        write_json_atomic(path, pruned)
    except (OSError, TypeError) as exc:
        # Non-fatal: the cache is a speed-up, so a failed write only costs a
        # re-lint next run.
        logger.debug("actionlint cache write failed for %s: %s", path, exc)


def _lint_cache_key(wf_bytes: bytes, version: str) -> str:
    """Return a cache key: sha256(workflow bytes + NUL + version string)."""
    h = hashlib.sha256()
    h.update(wf_bytes)
    h.update(b"\x00")
    h.update(version.encode())
    return h.hexdigest()


def _get_actionlint_version() -> str:
    """Return the ``actionlint --version`` string, cached in-process."""
    global _actionlint_version_cache
    if _actionlint_version_cache is not None:
        return _actionlint_version_cache
    try:
        proc = subprocess.run(
            ["actionlint", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        ver = (proc.stdout.strip() or proc.stderr.strip() or "unknown").splitlines()[0]
    except Exception:
        ver = "unknown"
    _actionlint_version_cache = ver
    return ver


@functools.cache
def _github_token_available() -> bool:
    """Whether org-policy lookups can authenticate, probed once per process.

    The policy fetch shells out to ``gh api``, so the question is whether ``gh``
    itself is authenticated rather than whether any particular variable is set;
    ``gh auth token`` answers exactly that and honors the same precedence gh
    uses for the real call. Any failure means no policy fetch, which is a
    skipped annotation rather than a failed run.
    """
    if not shutil.which("gh"):
        return False
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())


def discover_action_files(repo: Path) -> list[Path]:
    """Return every composite-action manifest under *repo*.

    A composite action's ``action.yml``/``action.yaml`` can live anywhere in
    the tree (a package's own root, ``.github/actions/*/``, …), unlike
    workflows which only live in ``.github/workflows/``, so this walks the
    whole repo rather than a fixed directory.
    """
    return sorted(
        p
        for pattern in ("action.yml", "action.yaml")
        for p in repo.rglob(pattern)
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    )


def _filter_excluded(paths: list[Path], repo: Path, patterns: list[str] | None) -> list[Path]:
    """Drop paths whose repo-relative directory matches an exclude glob."""
    if not patterns:
        return paths
    return [p for p in paths if not workspace_matches_exclude(repo, p.parent, patterns)]


# The newest tags answer everything a bump needs: the highest major, which
# majors have a bare vN, and the tag list. sha_to_tags only resolves a SHA pin
# whose `# vN` comment is missing, and a pin without one cannot be bumped
# safely anyway, so a page of recent tags is the whole job -- walking all 906
# tags of an action to learn its latest major was the cost with nothing behind it.
_GRAPHQL_TAG_PAGE = 100
# Aliases per request. The whole fleet fits in one round trip at this size,
# and a smaller batch only trades latency for more of them.
_GRAPHQL_BATCH = 46


def _tag_nodes_to_info(nodes: list[dict], *, truncated: bool = False) -> dict:
    """Build the action_tag_info shape from GraphQL ref nodes."""
    bare_majors: set[int] = set()
    all_majors: set[int] = set()
    all_tags: set[str] = set()
    sha_to_tags: dict[str, list[str]] = {}
    for node in nodes:
        name = (node or {}).get("name")
        target = (node or {}).get("target") or {}
        # An annotated tag's own oid is the tag object; the commit is one hop in,
        # and that is what the REST tags endpoint reports as commit.sha.
        inner = target.get("target") or {}
        sha = inner.get("oid") or target.get("oid")
        if not name or not sha:
            continue
        sha_to_tags.setdefault(sha.lower(), []).append(name)
        if re.fullmatch(r"v\d+", name):
            with contextlib.suppress(ValueError):
                bare_majors.add(int(name[1:]))
        m = _MAJOR_TAG_RE.match(name)
        if m:
            all_tags.add(name)
            with contextlib.suppress(ValueError):
                all_majors.add(int(m.group(1)))
    return {
        "bare_majors": bare_majors,
        "max_major": max(all_majors) if all_majors else None,
        "all_tags": all_tags,
        "sha_to_tags": sha_to_tags,
        # Older majors may sit past the page, so a "no bare vN" answer for a
        # major below the newest one is unknown rather than no.
        "truncated": truncated,
    }


# A leading ./ marks a composite action inside the repo, which has no GitHub
# repository to look up; matching it sent owner "." into the batch.
_SKIM_USES = re.compile(
    r"^\s*-?\s*uses\s*:\s*['\"]?"
    r"([A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*)",
    re.MULTILINE,
)


def skim_workflow_uses(path: Path) -> list[str]:
    """Owner/repo for every ``uses:`` in *path*, by regex rather than YAML parse.

    Only feeds the batched tag prefetch, so a miss costs a REST call and a
    false hit costs one wasted alias; neither needs the real parser's care.
    """
    return _SKIM_USES.findall(path.read_text(encoding="utf-8", errors="replace"))


def prefetch_tag_info(action_names: list[str], store: dict[str, dict | None]) -> None:
    """Fill *store* for many actions in one GraphQL round trip each batch.

    One REST call per action costs a round trip apiece; aliasing them into a
    single query collapses the fleet into one. Anything this cannot answer
    (over a page of tags, an unresolvable repo, any error at all) is simply
    left out, and the per-action REST path fills it in as before.
    """
    if not shutil.which("gh"):
        return
    repos = sorted(
        {"/".join(a.split("/")[:2]) for a in action_names if a.count("/") >= 1} - set(store)
    )
    for start in range(0, len(repos), _GRAPHQL_BATCH):
        chunk = repos[start : start + _GRAPHQL_BATCH]
        parts = []
        for idx, repo in enumerate(chunk):
            owner, _, name = repo.partition("/")
            if not owner or not name:
                continue
            parts.append(
                f'a{idx}: repository(owner: "{owner}", name: "{name}") {{ '
                f'refs(refPrefix: "refs/tags/", first: {_GRAPHQL_TAG_PAGE}, '
                "orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) "
                "{ totalCount nodes { name target { oid ... on Tag { target { oid } } } } } }"
            )
        if not parts:
            continue
        try:
            proc = run(
                ["gh", "api", "graphql", "-F", "query=@-"],
                Path.cwd(),
                timeout=60,
                stdin="query {" + " ".join(parts) + "}",
            )
            # A single unresolvable alias fails the whole request, and the
            # resolved ones still come back in `data` beside the errors.
            data = (json.loads(proc.stdout) or {}).get("data") or {}
        except (ValueError, OSError, subprocess.TimeoutExpired):
            continue
        for idx, repo in enumerate(chunk):
            node = data.get(f"a{idx}")
            refs = (node or {}).get("refs") or {}
            nodes = refs.get("nodes") or []
            if not node or not nodes:
                continue
            store[repo] = _tag_nodes_to_info(
                nodes, truncated=refs.get("totalCount", 0) > _GRAPHQL_TAG_PAGE
            )


def action_tag_info(action: str, *, cache: dict[str, dict | None] | None = None) -> dict | None:
    """Return tag info dict for a GitHub Action via ``gh api repos/<action>/tags``.

    Returns a dict with:
      - ``bare_majors``: set of integer majors that have a bare ``vN`` tag.
      - ``max_major``: maximum integer major across all version tags, or None.
      - ``all_tags``: set of every version tag name seen (``vN``, ``vN.M.P`` …).
      - ``sha_to_tags``: mapping of commit SHA → list of tag names.

    Returns None when ``gh`` is absent or the API call fails (caller should skip
    the pin — mirrors old graceful degradation).
    """
    if not shutil.which("gh"):
        return None
    store = _TAG_CACHE if cache is None else cache
    # Sub-path actions (owner/repo/setup-go) are tagged on owner/repo; the full path 404s.
    repo = "/".join(action.split("/")[:2])
    if repo in store:
        return store[repo]
    try:
        proc = run(
            [
                "gh",
                "api",
                f"repos/{repo}/tags",
                "--paginate",
                "--jq",
                r'.[] | "\(.name) \(.commit.sha)"',
            ],
            Path.cwd(),
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            store[repo] = None
            return None
        bare_majors: set[int] = set()
        all_majors: set[int] = set()
        all_tags: set[str] = set()
        sha_to_tags: dict[str, list[str]] = {}
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            name, sha = parts
            sha_to_tags.setdefault(sha.lower(), []).append(name)
            # Bare major: exactly "vN"
            if re.fullmatch(r"v\d+", name):
                with contextlib.suppress(ValueError):
                    bare_majors.add(int(name[1:]))
            m = _MAJOR_TAG_RE.match(name)
            if m:
                all_tags.add(name)
                with contextlib.suppress(ValueError):
                    all_majors.add(int(m.group(1)))
        max_major = max(all_majors) if all_majors else None
        result: dict = {
            "bare_majors": bare_majors,
            "max_major": max_major,
            "all_tags": all_tags,
            "sha_to_tags": sha_to_tags,
        }
        store[repo] = result
        return result
    except subprocess.TimeoutExpired:
        print(f"WARN: gh api timed out for {action}", file=sys.stderr)
    store[repo] = None
    return None


def _resolve_sha_major(sha: str, *, comment_hint: str | None, info: dict | None) -> int | None:
    """Resolve the floating major (integer N) a SHA pin belongs to.

    Prefers the ``# vN`` comment hint, else looks the SHA up in the tags API (full SHA or unique short-prefix). Returns
    ``None`` when the major cannot be determined.
    """
    if comment_hint:
        m = re.match(r"^v(\d+)", comment_hint)
        if m:
            return int(m.group(1))
    if info is None:
        return None
    sha_lower = sha.lower()
    if sha_lower.startswith("sha"):
        sha_lower = sha_lower[3:]
    sha_to_tags_map = info.get("sha_to_tags", {})
    tags_for_sha = sha_to_tags_map.get(sha_lower, [])
    if not tags_for_sha and len(sha_lower) < 40:
        matches = [k for k in sha_to_tags_map if k.startswith(sha_lower)]
        if len(matches) == 1:
            tags_for_sha = sha_to_tags_map[matches[0]]
    best_tuple: tuple[int, ...] = ()
    best_major: int | None = None
    for t in tags_for_sha:
        m2 = _MAJOR_TAG_RE.match(t)
        if not m2:
            continue
        tup = semver_tuple(t.lstrip("v"))
        if tup > best_tuple:
            best_tuple = tup
            best_major = int(m2.group(1))
    return best_major


def _tag_version(tag: str) -> tuple[int, ...]:
    """Semver tuple padded to three parts, so ``v1.10 == v1.10.0`` and ``v1.10 > v1.9``."""
    parts = semver_tuple(tag.lstrip("v"))
    return parts + (0,) * (3 - len(parts))


def _exact_tag_major(tag: str) -> int | None:
    m = _EXACT_TAG_RE.fullmatch(tag)
    return int(m.group(1)) if m else None


def _highest_exact_digest_in_major(info: dict, major: int) -> tuple[str, str] | None:
    """Return ``(commit_sha, tag)`` for the highest exact release tag within ``major``.

    Used by SHA-pin digest refresh: keeps an action digest-pinned but
    advances the SHA to the newest release within its current major, matching how
    Dependabot security-pins by commit. ``None`` when no exact tag exists in major.
    """
    best_tag: str | None = None
    best_sha: str | None = None
    best_tup: tuple[int, ...] = ()
    for sha, tags in info.get("sha_to_tags", {}).items():
        for t in tags:
            if _exact_tag_major(t) != major:
                continue
            tup = _tag_version(t)
            if tup > best_tup:
                best_tup = tup
                best_tag = t
                best_sha = sha
    if best_sha and best_tag:
        return best_sha, best_tag
    return None


def _comment_rewrite_for(comment: str | None, tag: str) -> tuple[str, str] | None:
    """Return the ``(old, new)`` version-hint rewrite so the comment names *tag*.

    A bare ``vN`` hint stays bare; a dotted hint becomes the full tag.
    """
    m = _MAJOR_TAG_RE.match(tag)
    if not comment or m is None:
        return None
    new = tag if "." in comment else f"v{m.group(1)}"
    return None if new == comment else (comment, new)


def _resolve_latest_target(info: dict) -> str | None:
    """Resolve the rewrite target for a cross-major bump.

    The floating ``v{max_major}`` is targeted only when a bare ``v{max_major}``
    tag exists upstream. Returns ``None`` when no major can be safely targeted
    (caller keeps report-only).
    """
    max_major = info.get("max_major")
    if max_major is None:
        return None
    if max_major in info.get("bare_majors", set()):
        return f"v{max_major}"
    return None


# ---------------------------------------------------------------------------
# Package version-ceiling ("hold") rules
# ---------------------------------------------------------------------------


def _resolve_actions_hold(repo: Path, package: str) -> HoldRule | None:
    """Return the active Actions hold for *package*, if any.

    Actions pins are resolved at the repo root, not per-directory workspace,
    so ``path`` and ``git_repo`` are the same resolved repo root.
    """
    repo_root = repo.resolve()
    ws = SimpleNamespace(path=repo_root, git_repo=repo_root, ecosystem="actions", manager="actions")
    return resolve_package_hold(ws, package)


def _target_within_hold(repo: Path, package: str, target_version: str) -> bool:
    """Return whether a resolved action tag is allowed by its active hold."""
    return target_within_hold(target_version, _resolve_actions_hold(repo, package))


def _action_tag_version(tag: str) -> tuple[int, ...] | None:
    if not _MAJOR_TAG_RE.match(tag):
        return None
    try:
        return _tag_version(tag)
    except Exception:
        return None


def _resolve_hold_action_tag_target(current_ref: str, tags: set[str], rule: HoldRule) -> str | None:
    """Return the highest action tag at or below *rule* when held back."""
    current_version = _action_tag_version(current_ref)
    if current_version is None or version_satisfies_ceiling(
        current_ref.lstrip("v"), rule.max_version
    ):
        return None

    candidates: list[tuple[tuple[int, ...], str]] = []
    for tag in tags:
        tag_version = _action_tag_version(tag)
        if tag_version is None:
            continue
        if version_satisfies_ceiling(tag.lstrip("v"), rule.max_version):
            candidates.append((tag_version, tag))
    if not candidates:
        return None
    target_version, target_tag = max(candidates, key=lambda item: (item[0], item[1]))
    if target_version >= current_version:
        return None
    return target_tag


def _matching_action_sha_tags(
    current_sha: str, info: dict
) -> list[tuple[tuple[int, ...], str, str]]:
    """Return exact release tags attached to *current_sha* (a floating ``vN`` names no release)."""
    normalized = current_sha.lower()
    if normalized.startswith("sha"):
        normalized = normalized[3:]
    sha_to_tags: dict[str, list[str]] = info.get("sha_to_tags", {})
    matching_keys: list[str] = []
    if normalized in sha_to_tags:
        matching_keys.append(normalized)
    elif len(normalized) < 40:
        matches = [key for key in sha_to_tags if key.startswith(normalized)]
        if len(matches) == 1:
            matching_keys = matches

    matching: list[tuple[tuple[int, ...], str, str]] = []
    for sha in matching_keys:
        for tag in sha_to_tags[sha]:
            if _exact_tag_major(tag) is not None:
                matching.append((_tag_version(tag), tag, sha))
    return matching


def _resolve_hold_action_sha_target(
    current_sha: str, info: dict, rule: HoldRule
) -> tuple[str, str, str] | None:
    """Return ``(target_sha, current_tag, target_tag)`` for a held SHA pin."""
    matching_tags = _matching_action_sha_tags(current_sha, info)
    if not matching_tags:
        return None
    current_version, current_tag, _ = max(matching_tags, key=lambda item: (item[0], item[1]))
    if version_satisfies_ceiling(current_tag.lstrip("v"), rule.max_version):
        return None

    candidates: list[tuple[tuple[int, ...], str, str]] = []
    for sha, tags in info.get("sha_to_tags", {}).items():
        for tag in tags:
            if _exact_tag_major(tag) is None:
                continue
            if version_satisfies_ceiling(tag.lstrip("v"), rule.max_version):
                candidates.append((_tag_version(tag), tag, sha))
    if not candidates:
        return None
    target_version, target_tag, target_sha = max(candidates, key=lambda item: (item[0], item[1]))
    if target_version >= current_version:
        return None
    return target_sha, current_tag, target_tag


def _enforce_version_ceilings(
    repo: Path,
    pins: list[Any],
    *,
    tag_cache: dict[str, dict | None] | None = None,
) -> list[PinReplacement]:
    """Return replacements that clamp held Actions pins."""
    replacements: list[PinReplacement] = []
    for uses in pins:
        action, pin = uses.action, uses.pin
        rule = _resolve_actions_hold(repo, action)
        if rule is None:
            continue

        info = action_tag_info(action, cache=tag_cache)
        if _SHA_RE.fullmatch(pin):
            if info is None:
                raise RuntimeError(
                    f"hold: failed to clamp {action} back to <={rule.max_version} "
                    "(actions): tag fetch failed while resolving SHA pin"
                )
            resolved = _resolve_hold_action_sha_target(pin, info, rule)
            if resolved is None:
                matching_tags = _matching_action_sha_tags(pin, info)
                if matching_tags:
                    _, current_tag, _ = max(matching_tags, key=lambda item: (item[0], item[1]))
                    if not version_satisfies_ceiling(current_tag.lstrip("v"), rule.max_version):
                        raise RuntimeError(
                            f"hold: failed to clamp {action} back to <={rule.max_version} "
                            "(actions): no available tag satisfies ceiling"
                        )
                continue
            target_sha, _, target_tag = resolved
            replacements.append(
                PinReplacement(
                    action=action,
                    pin=pin,
                    latest=target_sha,
                    line=uses.line,
                    comment_rewrite=_comment_rewrite_for(uses.comment, target_tag),
                )
            )
            continue

        current_version = _action_tag_version(pin)
        if current_version is None or version_satisfies_ceiling(pin.lstrip("v"), rule.max_version):
            continue
        if info is None:
            raise RuntimeError(
                f"hold: failed to clamp {action} back to <={rule.max_version} "
                "(actions): tag fetch failed while resolving tag pin"
            )
        target = _resolve_hold_action_tag_target(pin, info.get("all_tags", set()), rule)
        if target is None:
            raise RuntimeError(
                f"hold: failed to clamp {action} back to <={rule.max_version} "
                "(actions): no available tag satisfies ceiling"
            )
        replacements.append(
            PinReplacement(
                action=action,
                pin=pin,
                latest=target,
                line=uses.line,
            )
        )
    return replacements


def _run_actionlint(
    wf: Path,
    repo: Path,
    *,
    lint_cache: dict[str, dict] | None = None,
) -> tuple[str, str] | None:
    """Return ``(message, severity)`` on a finding, or None when lint passes/unavailable.

    *severity* is ``"error"`` (fatal) or ``"warning"`` (non-fatal shellcheck-only
    finding).  See :func:`_classify_actionlint_severity` for the classification
    logic.

    When *lint_cache* is provided,
    the result is looked up by content-hash before invoking the subprocess and
    stored after a cache miss.  The cache dict is mutated in place; the caller
    is responsible for persisting it to disk at the end of the run.
    """
    if not shutil.which("actionlint"):
        return None

    use_cache = lint_cache is not None

    wf_bytes: bytes | None = None
    cache_key: str | None = None

    if use_cache:
        try:
            wf_bytes = wf.read_bytes()
        except OSError:
            use_cache = False

    if use_cache and wf_bytes is not None:
        version = _get_actionlint_version()
        cache_key = _lint_cache_key(wf_bytes, version)
        entry = lint_cache.get(cache_key) if lint_cache is not None else None
        if entry is not None:
            # Update touch timestamp and return cached verdict.
            entry["ts"] = time.time()
            raw = entry.get("result")
            if isinstance(raw, str) and raw:
                # Default "error" for old cache entries missing the severity key.
                return (raw, entry.get("severity") or "error")
            return None

    # Cache miss (or cache disabled) — run the subprocess.
    try:
        proc = run(["actionlint", str(wf)], repo, timeout=60)
    except subprocess.TimeoutExpired:
        return ("actionlint timed out", "error")

    result_msg: str | None
    severity: str | None
    if proc.returncode != 0:
        result_msg = proc.stderr.strip() or proc.stdout.strip() or "actionlint failed"
        severity = _classify_actionlint_severity(result_msg)
    else:
        result_msg = None
        severity = None

    if use_cache and cache_key is not None and lint_cache is not None:
        lint_cache[cache_key] = {
            "result": result_msg,
            "severity": severity,
            "ts": time.time(),
        }

    if result_msg is not None and severity is not None:
        return (result_msg, severity)
    return None


def scan_actions(
    repo: Path,
    apply: bool,
    *,
    tag_cache: dict[str, dict | None] | None = None,
    org_policy_cache: dict[str, dict | None] | None = None,
    exclude_patterns: list[str] | None = None,
) -> tuple[list[ActionBump], int]:
    """Scan GitHub Actions workflow and composite-action files for bump recommendations.

    Parameters
    ----------
    repo:
        Repository root (parent of ``.github/workflows/``).
    apply:
        When True, rewrite workflow/action files for actionable bumps.
    tag_cache:
        Optional shared cache dict mapping action name → tag info (or None on
        failure).  When provided, entries are reused across calls so each
        (action, ref) is resolved at most once per run.  When omitted, a fresh
        per-call dict is used (preserves test isolation).
    org_policy_cache:
        Optional shared policy cache dict (maps owner → policy dict or None).
        When ``None``, the module-level ``_ORG_POLICY_CACHE`` is used.  Callers
        may pass a fresh ``{}`` for test isolation.  Policy checks run only when
        ``gh`` is authenticated; otherwise they are skipped (zero ``gh`` calls).
    exclude_patterns:
        Repo-relative glob patterns (the repo's own ``.repo-ops.toml``
        ``exclude``) whose matching workflow/action files are neither scanned
        nor rewritten.
    """
    if not shutil.which("actionlint"):
        print("WARN: actionlint not on PATH; skipping action validation", file=sys.stderr)
    workflows = _filter_excluded(discover_workflow_files(repo), repo, exclude_patterns)
    action_files = _filter_excluded(discover_action_files(repo), repo, exclude_patterns)
    targets: list[tuple[Path, bool]] = [(w, True) for w in workflows] + [
        (a, False) for a in action_files
    ]
    bumps: list[ActionBump] = []
    _cache: dict[str, dict | None] = tag_cache if tag_cache is not None else {}

    # Org allowed-actions policy: report-only rows whenever gh can authenticate.
    _policy_enabled = _github_token_available()
    _policy_store: dict[str, dict | None] = (
        org_policy_cache if org_policy_cache is not None else _ORG_POLICY_CACHE
    )
    _org_owner: str | None = None
    _org_policy: dict | None = None
    if _policy_enabled:
        _org_owner = _derive_owner(repo)
        if _org_owner is not None:
            _org_policy = _fetch_org_policy_cached(_org_owner, _policy_store)

    def _policy_violation_for(action: str) -> str | None:
        if _org_policy is None or _org_owner is None or action.startswith("./"):
            return None
        return _action_policy_violation(action, _org_policy, _org_owner)

    # Load the actionlint result cache once per run (skipped in "off" mode).
    _lint_cache_path = _actionlint_cache_path()
    _lint_cache = _load_lint_cache(_lint_cache_path)

    for wf, is_workflow in targets:
        rel = str(wf.relative_to(repo))
        # actionlint validates workflow YAML shape (jobs/steps); a composite
        # action.yml has a different top-level shape (runs.steps) and is not
        # a lint target, but its `uses:` pins are scanned the same way.
        lint_res = _run_actionlint(wf, repo, lint_cache=_lint_cache) if is_workflow else None
        if lint_res:
            lint_msg, lint_sev = lint_res
            fatal = lint_sev == "error"
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(actionlint)",
                    pin="",
                    latest="",
                    status="fail" if fatal else "warn",
                    detail=lint_msg,
                )
            )
            if fatal:
                print(
                    f"WARN: {rel}: actionlint reported errors; pin bumps skipped for this workflow",
                    file=sys.stderr,
                )
                continue
            # Warning-class finding: record the warn row and proceed.
            print(
                f"WARN: {rel}: actionlint reported warnings only; proceeding with pin bumps",
                file=sys.stderr,
            )
        try:
            pins = parse_workflow_uses(wf, workflow_rel=rel)
        except WorkflowParseError as exc:
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(yaml)",
                    pin="",
                    latest="",
                    status="fail",
                    detail=str(exc),
                )
            )
            continue
        replacements: list[PinReplacement] = []
        for uses in pins:
            action, pin = uses.action, uses.pin
            # SHA-pinned: refresh the digest, staying SHA-pinned.
            if _SHA_RE.fullmatch(pin):
                # Digest refresh: stay SHA-pinned but advance the digest
                # to the newest in-major release, preserving the # vN comment.
                info = action_tag_info(action, cache=_cache)
                major = (
                    _resolve_sha_major(pin, comment_hint=uses.comment, info=info)
                    if info is not None
                    else None
                )
                # Cross-major SHA refresh: advance to max_major if it has a
                # real vN.M.P release.
                # Falls back to the current major when the newest major has
                # no exact tag.
                target_major = major
                if info is not None and major is not None:
                    max_major = info.get("max_major")
                    if max_major is not None and max_major > major:
                        candidate = _highest_exact_digest_in_major(info, max_major)
                        if candidate is not None:
                            target_major = max_major
                digest = (
                    _highest_exact_digest_in_major(info, target_major)
                    if info is not None and target_major is not None
                    else None
                )
                if digest is not None:
                    new_sha, tag = digest
                    pin_norm = pin.lower()
                    if pin_norm.startswith("sha"):
                        pin_norm = pin_norm[3:]
                    already_current = pin_norm == new_sha or (
                        len(pin_norm) < 40 and new_sha.startswith(pin_norm)
                    )
                    if not already_current and _target_within_hold(repo, action, tag):
                        cross_major = (
                            target_major is not None and major is not None and target_major != major
                        )
                        if cross_major:
                            detail_desc = (
                                f"cross-major SHA re-pin to {tag} "
                                f"(v{major} -> v{target_major}, {new_sha[:12]})"
                            )
                        else:
                            detail_desc = (
                                f"refresh SHA digest to {tag} within v{major} ({new_sha[:12]})"
                            )
                        bumps.append(
                            ActionBump(
                                workflow=rel,
                                action=action,
                                pin=pin,
                                latest=new_sha,
                                status="ok",
                                detail=detail_desc,
                            )
                        )
                        if apply:
                            replacements.append(
                                PinReplacement(
                                    action=action,
                                    pin=pin,
                                    latest=new_sha,
                                    line=uses.line,
                                    comment_rewrite=_comment_rewrite_for(uses.comment, tag),
                                )
                            )
                        continue
                # Unresolvable major / no newer digest → report only below.
                info = action_tag_info(action, cache=_cache)
                hold_rule = _resolve_actions_hold(repo, action)
                if info is not None and hold_rule is not None:
                    hold_resolution = _resolve_hold_action_sha_target(pin, info, hold_rule)
                    if hold_resolution is not None:
                        target_sha, current_tag, target_tag = hold_resolution
                        bumps.append(
                            ActionBump(
                                workflow=rel,
                                action=action,
                                pin=pin,
                                latest=target_sha,
                                status="ok",
                                detail=(
                                    f"hold clamp {current_tag} -> {target_tag} "
                                    f"(<={hold_rule.max_version})"
                                ),
                            )
                        )
                        continue
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=pin,
                        status="ok",
                        detail="SHA-pinned; report only",
                    )
                )
                continue

            # Parse current major from pin (vN, vN.x.y). Skip non-version refs.
            m = re.match(r"^v(\d+)", pin)
            if not m:
                continue
            M = int(m.group(1))
            desired = f"v{M}"

            info = action_tag_info(action, cache=_cache)
            if info is None:
                # gh absent or API failed — skip this pin.
                continue

            # Pin a floating tag to a commit SHA, crossing to the newest major
            # when it has a real vN.M.P release. Runs before the tag-normalize
            # logic below; once SHA-pinned, the refresh path above owns it.
            target_major = M
            latest_major = info.get("max_major")
            if (
                latest_major is not None
                and latest_major > M
                and _highest_exact_digest_in_major(info, latest_major) is not None
            ):
                target_major = latest_major
            digest = _highest_exact_digest_in_major(info, target_major)
            if digest is not None:
                new_sha, tag_label = digest
                if _target_within_hold(repo, action, tag_label):
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=new_sha,
                            status="ok",
                            detail=f"pin floating {pin} to SHA ({tag_label}, {new_sha[:12]})",
                        )
                    )
                    if apply:
                        replacements.append(
                            PinReplacement(
                                action=action,
                                pin=pin,
                                latest=new_sha,
                                line=uses.line,
                                set_comment=f"# {tag_label}",
                            )
                        )
                    continue
            # Unresolvable digest for this major — fall through to the
            # tag-normalize logic below (report only; stays tag-pinned).

            if (
                info.get("truncated")
                and info.get("max_major") != M
                and M not in info["bare_majors"]
                and (full := action_tag_info(action, cache={})) is not None
            ):
                info = full
            bare_major_exists = M in info["bare_majors"]
            max_major = info["max_major"]
            hold_rule = _resolve_actions_hold(repo, action)
            if hold_rule is not None:
                hold_target = _resolve_hold_action_tag_target(
                    pin, info.get("all_tags", set()), hold_rule
                )
                if hold_target is not None:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=hold_target,
                            status="ok",
                            detail=(
                                f"hold clamp {pin} -> {hold_target} (<={hold_rule.max_version})"
                            ),
                        )
                    )
                    continue

            # A newer major upstream is rewritten (not just reported),
            # provided it can be safely targeted.
            newer_major = max_major is not None and max_major > M
            latest_target = _resolve_latest_target(info) if newer_major else None
            if latest_target and not _target_within_hold(repo, action, latest_target):
                latest_target = None

            if latest_target and latest_target != pin:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=latest_target,
                        status="ok",
                        detail=f"cross-major {pin} -> {latest_target}",
                    )
                )
                if apply:
                    replacements.append(
                        PinReplacement(
                            action=action,
                            pin=pin,
                            latest=latest_target,
                            line=uses.line,
                        )
                    )
            elif pin == desired:
                # Already at floating major. Report newer major if available.
                if newer_major:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=f"v{max_major}",
                            status="ok",
                            detail=f"newer major v{max_major} available; not auto-applied (report only)",
                        )
                    )
                # else: up to date, emit nothing
            else:
                # Exact/minor pin (e.g. v3.19.0) — try to collapse to floating major.
                hold_has_finer_ceiling = (
                    hold_rule is not None and len(semver_tuple(hold_rule.max_version)) > 1
                )
                if (
                    bare_major_exists
                    and not hold_has_finer_ceiling
                    and _target_within_hold(repo, action, desired)
                ):
                    detail = f"normalize to floating major v{M}"
                    if newer_major:
                        detail += f"; newer major v{max_major} available"
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=desired,
                            status="ok",
                            detail=detail,
                        )
                    )
                    if apply:
                        replacements.append(
                            PinReplacement(
                                action=action,
                                pin=pin,
                                latest=desired,
                                line=uses.line,
                            )
                        )
                else:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=pin,
                            status="ok",
                            detail="no floating major tag upstream; report only",
                        )
                    )
        if apply:
            replacements.extend(_enforce_version_ceilings(repo, pins, tag_cache=_cache))
        if apply and replacements:
            try:
                update_workflow_pins(wf, replacements)
            except WorkflowParseError as exc:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action="(yaml)",
                        pin="",
                        latest="",
                        status="fail",
                        detail=str(exc),
                    )
                )

        # Org allowed-actions policy check.
        if _org_policy is not None and _org_owner is not None:
            for uses in pins:
                action = uses.action
                violation = _policy_violation_for(action)
                if violation:
                    status = "policy"
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=uses.pin,
                            latest=uses.pin,
                            status=status,
                            detail=violation,
                        )
                    )

    # Persist the actionlint cache (atomic write; best-effort).
    _save_lint_cache(_lint_cache_path, _lint_cache)

    return bumps, len(targets)
