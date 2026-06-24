"""GitHub Actions workflow pin audit and apply."""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from repo_ops_core.process import run
from repo_ops_core.version_utils import semver_tuple
from repo_ops_models import ActionBump

from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    WorkflowParseError,
    parse_workflow_uses,
    update_workflow_pins,
)

_TAG_CACHE: dict[str, dict | None] = {}

# ---------------------------------------------------------------------------
# Org allowed-actions policy cache (issue #124)
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

# Full 40-hex SHA or short sha<hex> prefix
_SHA_RE = re.compile(r"^(?:sha[0-9a-f]+|[0-9a-f]{7,40})$", re.IGNORECASE)

TagStyle = Literal["major", "exact"]

# How the Actions upgrader treats a pin whose major is behind the newest upstream
# major line. ``normalize`` (default) only collapses to the current floating major
# and reports newer majors (report only). ``latest`` also rewrites the pin to the
# newest available major (issue #106), subject to the same tag-style + bare-major
# safety rules used elsewhere.
ActionsUpgradeMode = Literal["normalize", "latest"]

# How actionlint failures gate pin bumps for a workflow.
# ``block`` (default) — a lint failure records a fail row and skips all pin
#   bumps for that workflow (current behaviour).
# ``warn`` — records the ActionBump fail row and prints a WARN to stderr, but
#   still proceeds with pin bumps for that workflow.
# ``off`` — skips running actionlint entirely.
# Env var: REPO_OPS_ACTIONS_LINT_LEVEL (values: block | warn | off).
# A CLI flag (--actions-lint-level) can wire into this parameter later.
ActionsLintLevel = Literal["block", "warn", "off"]

_DEFAULT_LINT_LEVEL: ActionsLintLevel = "block"

# ---------------------------------------------------------------------------
# Actionlint result cache (issue #123)
# ---------------------------------------------------------------------------

# Maximum age (seconds) for a cache entry before it is pruned on write.
_LINT_CACHE_MAX_AGE_S: int = 90 * 24 * 3600  # 90 days

# Maximum number of entries kept in the cache file (simple LRU-by-touch).
_LINT_CACHE_MAX_ENTRIES: int = 4096

# In-process version string cache (populated on first _run_actionlint call).
_actionlint_version_cache: str | None = None


def _derive_owner(repo: Path) -> str | None:
    """Return the GitHub org/user name from the repo's origin remote URL.

    Parses both HTTPS (``https://github.com/owner/repo.git``) and SSH
    (``git@github.com:owner/repo.git``) forms.  Returns ``None`` when:
    - ``git`` is not on PATH,
    - the remote is absent or malformed,
    - or the remote does not point at github.com.
    """
    if not shutil.which("git"):
        return None
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip()
    if not url:
        return None

    # HTTPS: https://github.com/owner/repo[.git]
    m = re.match(r"https?://github\.com/([^/]+)/", url)
    if m:
        return m.group(1)

    # SSH: git@github.com:owner/repo[.git]
    m2 = re.match(r"git@github\.com:([^/]+)/", url)
    if m2:
        return m2.group(1)

    return None


def _action_policy_violation(action: str, policy: dict) -> str | None:
    """Return a human-readable reason string when *action* is blocked by *policy*.

    Returns ``None`` when the action is allowed.

    Rules:
    - ``enabled=False`` → every action is blocked.
    - ``allowed_actions=all`` → nothing blocked.
    - ``allowed_actions=local_only`` → only same-owner or path-local actions
      (``./`` prefix) are allowed.
    - ``allowed_actions=selected`` → allowed when:
        - ``github_owned_allowed`` and the action owner is ``actions`` or
          ``github``, OR
        - the action matches any glob in ``patterns_allowed`` (fnmatch semantics
          per GitHub docs: ``owner/*`` matches ``owner/any-repo``).
    """
    allowed_actions = policy.get("allowed_actions", "all")

    if not policy.get("enabled", True):
        return f"org policy (disabled): {action} not allowed — Actions are disabled for this org"

    if allowed_actions == "all":
        return None

    # Path-local actions (``./``) are always allowed regardless of policy mode.
    if action.startswith("./"):
        return None

    if allowed_actions == "local_only":
        # Only same-owner (owner/*) refs are local; we don't know the owner of
        # the calling repo here — the caller must pass the owner for comparison.
        # Instead, we key off the ``_owner_of_action`` helper below.
        return (
            f"org policy (local_only): {action} not allowed"
            " — workflows using it will fail at startup"
        )

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

    # Unknown mode — be permissive.
    return None


def _action_policy_violation_local_only(
    action: str,
    policy: dict,
    org_owner: str,
) -> str | None:
    """Variant of ``_action_policy_violation`` for ``local_only`` mode.

    Receives the org *owner* so same-owner actions can be distinguished from
    cross-org refs.  Same-owner actions (``owner/repo``) are allowed;
    cross-org refs are blocked.
    """
    if not policy.get("enabled", True):
        return f"org policy (disabled): {action} not allowed — Actions are disabled for this org"
    allowed_actions = policy.get("allowed_actions", "all")
    if allowed_actions == "all":
        return None
    if action.startswith("./"):
        return None
    if allowed_actions == "local_only":
        action_owner = action.split("/")[0] if "/" in action else ""
        if action_owner.lower() == org_owner.lower():
            return None  # same-org action — allowed
        return (
            f"org policy (local_only): {action} not allowed"
            " — workflows using it will fail at startup"
        )
    # Delegate to the generic checker for other modes.
    return _action_policy_violation(action, policy)


def _fetch_org_policy_cached(owner: str, store: dict[str, dict | None]) -> dict | None:
    """Return cached org policy for *owner*, fetching via ``gh api`` on miss.

    Identical in behaviour to ``_fetch_org_policy`` but uses *store* instead of
    the module-level ``_ORG_POLICY_CACHE``, allowing test isolation via an
    injected ``{}``.
    """
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
                    policy["github_owned_allowed"] = bool(
                        sel.get("github_owned_allowed", False)
                    )
                    policy["verified_allowed"] = bool(
                        sel.get("verified_allowed", False)
                    )
                    patterns = sel.get("patterns_allowed")
                    if isinstance(patterns, list):
                        policy["patterns_allowed"] = [
                            str(p) for p in patterns if isinstance(p, str)
                        ]
        except Exception:  # noqa: S110 — best-effort: selected-actions fetch is non-fatal
            pass

    store[owner] = policy
    return policy


def _actionlint_cache_path() -> Path:
    """Return ``$XDG_CACHE_HOME/repo-ops/actionlint-cache.json``."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return Path(base) / "repo-ops" / "actionlint-cache.json"


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
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pruned, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: S110 — best-effort: cache write failure is non-fatal
        pass


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


def _read_lint_level_env() -> ActionsLintLevel:
    """Read ``REPO_OPS_ACTIONS_LINT_LEVEL`` from the environment.

    Returns ``"block"`` when the variable is absent or holds an unrecognised
    value (with a warning printed to stderr in the latter case).
    """
    raw = os.environ.get("REPO_OPS_ACTIONS_LINT_LEVEL", "").strip().lower()
    if not raw:
        return _DEFAULT_LINT_LEVEL
    if raw in ("block", "warn", "off"):
        return raw  # type: ignore[return-value]
    print(
        f"WARN: REPO_OPS_ACTIONS_LINT_LEVEL={raw!r} unrecognised; "
        "defaulting to 'block'",
        file=sys.stderr,
    )
    return _DEFAULT_LINT_LEVEL


def discover_workflow_files(repo: Path) -> list[Path]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def action_tag_info(
    action: str, *, cache: dict[str, dict | None] | None = None
) -> dict | None:
    """Return tag info dict for a GitHub Action via ``gh api repos/<action>/tags``.

    Returns a dict with:
      - ``bare_majors``: set of integer majors that have a bare ``vN`` tag.
      - ``max_major``: maximum integer major across all version tags, or None.
      - ``all_tags``: set of every version tag name seen (``vN``, ``vN.M.P`` …).
        Source of truth for highest-exact-in-major lookups, since ``sha_to_tags``
        is empty when the API/mock returns name-only lines.
      - ``sha_to_tags``: mapping of commit SHA → list of tag names (for SHA unpin).
        Each line from the API is parsed as "name sha" or "name" (name-only for
        backwards compat with existing test mocks that return names only).

    Returns None when ``gh`` is absent or the API call fails (caller should skip
    the pin — mirrors old graceful degradation).
    """
    if not shutil.which("gh"):
        return None
    store = _TAG_CACHE if cache is None else cache
    if action in store:
        return store[action]
    try:
        proc = run(
            [
                "gh",
                "api",
                f"repos/{action}/tags",
                "--paginate",
                "--jq",
                r'.[] | "\(.name) \(.commit.sha)"',
            ],
            Path.cwd(),
            timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            store[action] = None
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
            name = parts[0]
            sha = parts[1] if len(parts) > 1 else None
            if sha:
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
        store[action] = result
        return result
    except subprocess.TimeoutExpired:
        print(f"WARN: gh api timed out for {action}", file=sys.stderr)
    store[action] = None
    return None


def _resolve_sha_target(
    sha: str,
    *,
    comment_hint: str | None,
    info: dict | None,
    tag_style: TagStyle,
) -> tuple[str, str] | None:
    """Resolve a SHA pin to a target tag string and a human description.

    Returns ``(target_tag, description)`` or ``None`` when unresolvable.

    Resolution order:
    1. Comment hint ``# vN[.N.N]``: use major N (e.g. ``v4``) as the major.
    2. SHA→tag mapping from the tags API: find tags pointing at this SHA, pick
       the highest semver one to determine the major.

    When ``tag_style='exact'``, ``target_tag`` is the highest ``vN.M.P`` within
    the resolved major (or ``@vN`` fallback when no exact tags exist).
    When ``tag_style='major'``, ``target_tag`` is ``vN``.
    """
    # --- Step 1: resolve major ---
    resolved_major: int | None = None
    source_desc = ""

    if comment_hint:
        m = re.match(r"^v(\d+)", comment_hint)
        if m:
            resolved_major = int(m.group(1))
            source_desc = f"comment hint {comment_hint}"

    if resolved_major is None and info is not None:
        sha_lower = sha.lower()
        # Normalise sha prefix-form: "sha<hex>" → strip "sha" prefix
        if sha_lower.startswith("sha"):
            sha_lower = sha_lower[3:]
        sha_to_tags_map = info.get("sha_to_tags", {})
        tags_for_sha = sha_to_tags_map.get(sha_lower, [])
        if not tags_for_sha and len(sha_lower) < 40:
            # Short-SHA prefix fallback: scan all full SHAs for a unique prefix match.
            matches = [k for k in sha_to_tags_map if k.startswith(sha_lower)]
            if len(matches) == 1:
                tags_for_sha = sha_to_tags_map[matches[0]]
            # len == 0: no match; len > 1: ambiguous — leave tags_for_sha empty.
        if tags_for_sha:
            # Pick the highest semver among tags pointing at this SHA.
            best: str | None = None
            best_tuple: tuple[int, ...] = ()
            for t in tags_for_sha:
                if not _MAJOR_TAG_RE.match(t):
                    continue
                stripped = t.lstrip("v")
                tup = semver_tuple(stripped)
                if tup > best_tuple:
                    best_tuple = tup
                    best = t
            if best:
                m2 = _MAJOR_TAG_RE.match(best)
                if m2:
                    resolved_major = int(m2.group(1))
                    source_desc = f"SHA resolves to {best}"

    if resolved_major is None:
        return None

    major_tag = f"v{resolved_major}"

    # --- Step 2: pick target based on tag_style ---
    if tag_style == "major":
        return major_tag, f"unpin SHA to {major_tag} (via {source_desc})"

    # exact: find highest vN.M.P within this major
    exact_tag: str | None = None
    if info is not None:
        best_exact: str | None = None
        best_exact_tup: tuple[int, ...] = ()
        for sha_tags in info.get("sha_to_tags", {}).values():
            for t in sha_tags:
                m3 = _MAJOR_TAG_RE.match(t)
                if not m3 or int(m3.group(1)) != resolved_major:
                    continue
                if re.fullmatch(r"v\d+\.\d+\.\d+.*", t):
                    stripped = t.lstrip("v")
                    tup = semver_tuple(stripped)
                    if tup > best_exact_tup:
                        best_exact_tup = tup
                        best_exact = t
        # Also scan all_majors tags from sha_to_tags full names list
        # (may include tags on different commits within the same major)
        # We need ALL tags for the major, not just ones mapping to this SHA.
        # The info dict doesn't directly provide this, so we build it from sha_to_tags.
        all_in_major: list[str] = []
        for tags_list in info.get("sha_to_tags", {}).values():
            for t in tags_list:
                m4 = _MAJOR_TAG_RE.match(t)
                if (
                    m4
                    and int(m4.group(1)) == resolved_major
                    and re.fullmatch(r"v\d+\.\d+\.\d+.*", t)
                ):
                    all_in_major.append(t)
        if all_in_major:
            best_in_major = max(all_in_major, key=lambda t: semver_tuple(t.lstrip("v")))
            best_in_major_tup = semver_tuple(best_in_major.lstrip("v"))
            if best_in_major_tup > best_exact_tup:
                best_exact = best_in_major
        exact_tag = best_exact

    target = exact_tag if exact_tag else major_tag
    style_note = "exact" if exact_tag else "major (no exact tag found)"
    return target, f"unpin SHA to {target} ({style_note}; via {source_desc})"


def _resolve_sha_major(
    sha: str, *, comment_hint: str | None, info: dict | None
) -> int | None:
    """Resolve the floating major (integer N) a SHA pin belongs to.

    Mirrors step 1 of ``_resolve_sha_target``: prefer the ``# vN`` comment hint,
    else look the SHA up in the tags API (full SHA or unique short-prefix). Returns
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


def _highest_exact_digest_in_major(info: dict, major: int) -> tuple[str, str] | None:
    """Return ``(commit_sha, tag)`` for the highest ``vN.M.P`` tag within ``major``.

    Used by SHA-pin digest refresh (#109): keeps an action digest-pinned but
    advances the SHA to the newest release within its current major, matching how
    Dependabot security-pins by commit. ``None`` when no exact tag exists in major.
    """
    best_tag: str | None = None
    best_sha: str | None = None
    best_tup: tuple[int, ...] = ()
    for sha, tags in info.get("sha_to_tags", {}).items():
        for t in tags:
            m = re.fullmatch(r"v(\d+)\.\d+\.\d+.*", t)
            if not m or int(m.group(1)) != major:
                continue
            tup = semver_tuple(t.lstrip("v"))
            if tup > best_tup:
                best_tup = tup
                best_tag = t
                best_sha = sha
    if best_sha and best_tag:
        return best_sha, best_tag
    return None


def _highest_exact_in_major(info: dict, major: int) -> str | None:
    """Return the highest ``vN.M.P`` tag within ``major`` from ``info['all_tags']``."""
    best: str | None = None
    best_tup: tuple[int, ...] = ()
    for t in info.get("all_tags", ()):
        m = re.fullmatch(r"v(\d+)\.\d+\.\d+.*", t)
        if not m or int(m.group(1)) != major:
            continue
        tup = semver_tuple(t.lstrip("v"))
        if tup > best_tup:
            best_tup = tup
            best = t
    return best


def _resolve_latest_target(info: dict, tag_style: TagStyle) -> str | None:
    """Resolve the rewrite target for a cross-major (latest-mode) bump.

    Honors ``tag_style`` and the same bare-major safety rule used elsewhere:
    - ``exact`` → highest ``v{max_major}.M.P`` (falls back to floating ``v{max_major}``
      only when no exact tag exists but a bare major tag does).
    - ``major`` → floating ``v{max_major}`` only when a bare ``v{max_major}`` tag
      exists upstream.

    Returns ``None`` when no major can be safely targeted (caller keeps report-only).
    """
    max_major = info.get("max_major")
    if max_major is None:
        return None
    if tag_style == "exact":
        exact = _highest_exact_in_major(info, max_major)
        if exact:
            return exact
        # No exact tag for the newest major — fall back to floating major below.
    if max_major in info.get("bare_majors", set()):
        return f"v{max_major}"
    return None


def _run_actionlint(
    wf: Path,
    repo: Path,
    *,
    lint_cache: dict[str, dict] | None = None,
) -> str | None:
    """Return a lint error string, or None when lint passes or is unavailable.

    When *lint_cache* is provided (and ``REPO_OPS_NO_LINT_CACHE`` is unset),
    the result is looked up by content-hash before invoking the subprocess and
    stored after a cache miss.  The cache dict is mutated in place; the caller
    is responsible for persisting it to disk at the end of the run.
    """
    if not shutil.which("actionlint"):
        return None

    use_cache = lint_cache is not None and not os.environ.get("REPO_OPS_NO_LINT_CACHE")

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
            return raw if isinstance(raw, str) else None

    # Cache miss (or cache disabled) — run the subprocess.
    try:
        proc = run(["actionlint", str(wf)], repo, timeout=60)
    except subprocess.TimeoutExpired:
        return "actionlint timed out"

    if proc.returncode != 0:
        result: str | None = (
            proc.stderr.strip() or proc.stdout.strip() or "actionlint failed"
        )
    else:
        result = None

    if use_cache and cache_key is not None and lint_cache is not None:
        lint_cache[cache_key] = {
            "result": result,
            "ts": time.time(),
        }

    return result


def scan_actions(
    repo: Path,
    apply: bool,
    *,
    keep_sha: bool = True,
    refresh_sha: bool = False,
    tag_style: TagStyle = "major",
    upgrade_mode: ActionsUpgradeMode = "normalize",
    tag_cache: dict[str, dict | None] | None = None,
    lint_level: ActionsLintLevel | None = None,
    org_policy_cache: dict[str, dict | None] | None = None,
) -> tuple[list[ActionBump], int]:
    """Scan GitHub Actions workflow files and produce bump recommendations.

    Parameters
    ----------
    repo:
        Repository root (parent of ``.github/workflows/``).
    apply:
        When True, rewrite workflow files for actionable bumps.
    keep_sha:
        When True (the default), SHA-pinned actions are reported only and never
        rewritten — preserving supply-chain hardening.  Pass ``keep_sha=False``
        (CLI: ``--actions-unpin-sha``) to opt in to resolving SHA pins to
        version tags.
    refresh_sha:
        When True (CLI: ``--actions-refresh-sha``) and the action stays SHA-pinned
        (``keep_sha=True``), advance the digest to the commit of the newest
        ``vN.M.P`` release within the pin's resolved major, preserving the digest
        pin and the ``# vN`` comment (Dependabot-compatible, issue #109). When
        combined with ``upgrade_mode="latest"``, the digest may also cross a major
        boundary: if the newest major has a real ``vN.M.P`` release the pin is
        re-pinned to that commit and the ``# vN`` comment is updated to the new
        major (#138). Ignored when ``keep_sha=False`` (the unpin path takes over).
        No-op when the major cannot be resolved or no newer digest exists.
    tag_style:
        ``"major"`` (default) → rewrite SHA pins to ``@vN``.
        ``"exact"`` → rewrite to the highest ``@vN.M.P`` within the resolved major.
    upgrade_mode:
        ``"normalize"`` (default) → only collapse to the current floating major;
        a newer major upstream is reported only (not applied).
        ``"latest"`` (CLI: ``--actions-upgrade-mode=latest`` / ``--latest``) → also
        rewrite a pin to the newest available major, honoring ``tag_style`` and the
        bare-major safety rule. Falls back to normalize/report-only when the newest
        major cannot be safely targeted.
    tag_cache:
        Optional shared cache dict mapping action name → tag info (or None on
        failure).  When provided, entries are reused across calls so each
        (action, ref) is resolved at most once per run.  When omitted, a fresh
        per-call dict is used (preserves test isolation).
    lint_level:
        Controls how actionlint failures gate pin bumps.  ``None`` (default) reads
        from the ``REPO_OPS_ACTIONS_LINT_LEVEL`` environment variable, falling
        back to ``"block"``.

        * ``"block"`` — lint failure records a fail row and skips all pin bumps
          for that workflow (current behaviour).
        * ``"warn"`` — records the fail row and prints a WARN to stderr, but
          proceeds with pin bumps for the workflow.
        * ``"off"`` — skips running actionlint entirely.

        A CLI flag (``--actions-lint-level``) can wire into this parameter later.
    org_policy_cache:
        Optional shared policy cache dict (maps owner → policy dict or None).
        When ``None``, the module-level ``_ORG_POLICY_CACHE`` is used.  Callers
        may pass a fresh ``{}`` for test isolation.  The env var
        ``REPO_OPS_ACTIONS_ORG_POLICY=1`` must be set for any policy fetching
        to occur; when absent, policy checks are skipped entirely (zero ``gh``
        calls).
    """
    effective_lint_level: ActionsLintLevel = (
        lint_level if lint_level is not None else _read_lint_level_env()
    )
    if effective_lint_level != "off" and not shutil.which("actionlint"):
        print(
            "WARN: actionlint not on PATH; skipping action validation", file=sys.stderr
        )
    workflows = discover_workflow_files(repo)
    bumps: list[ActionBump] = []
    _cache: dict[str, dict | None] = tag_cache if tag_cache is not None else {}

    # Org allowed-actions policy (opt-in via REPO_OPS_ACTIONS_ORG_POLICY=1).
    _policy_enabled: bool = (
        os.environ.get("REPO_OPS_ACTIONS_ORG_POLICY", "").strip() == "1"
    )
    _policy_store: dict[str, dict | None] = (
        org_policy_cache if org_policy_cache is not None else _ORG_POLICY_CACHE
    )
    _org_owner: str | None = None
    _org_policy: dict | None = None
    if _policy_enabled:
        _org_owner = _derive_owner(repo)
        if _org_owner is not None:
            _org_policy = _fetch_org_policy_cached(_org_owner, _policy_store)

    # Load the actionlint result cache once per run (skipped in "off" mode).
    _lint_cache: dict[str, dict] | None = None
    _lint_cache_path: Path | None = None
    if effective_lint_level != "off":
        _lint_cache_path = _actionlint_cache_path()
        _lint_cache = _load_lint_cache(_lint_cache_path)

    for wf in workflows:
        rel = str(wf.relative_to(repo))
        lint_err = (
            None
            if effective_lint_level == "off"
            else _run_actionlint(wf, repo, lint_cache=_lint_cache)
        )
        if lint_err:
            bumps.append(
                ActionBump(
                    workflow=rel,
                    action="(actionlint)",
                    pin="",
                    latest="",
                    status="fail",
                    detail=lint_err,
                )
            )
            if effective_lint_level == "block":
                print(
                    f"WARN: {rel}: actionlint failed; pin bumps skipped for this workflow",
                    file=sys.stderr,
                )
                continue
            # warn: record fail row above, print WARN, but proceed with pin bumps.
            print(
                f"WARN: {rel}: actionlint failed (lint_level=warn); proceeding with pin bumps",
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
            # SHA-pinned: attempt to resolve to a version tag (unless keep_sha).
            if _SHA_RE.fullmatch(pin):
                if keep_sha:
                    # Digest refresh (#109): stay SHA-pinned but advance the digest
                    # to the newest in-major release, preserving the # vN comment.
                    if refresh_sha:
                        info = action_tag_info(action, cache=_cache)
                        major = (
                            _resolve_sha_major(
                                pin, comment_hint=uses.comment, info=info
                            )
                            if info is not None
                            else None
                        )
                        # Cross-major SHA refresh (#138): when upgrade_mode=latest,
                        # advance to max_major if it has a real vN.M.P release.
                        # Falls back to the current major when the newest major has
                        # no exact tag (same safety check as the unpin path).
                        target_major = major
                        if (
                            upgrade_mode == "latest"
                            and info is not None
                            and major is not None
                        ):
                            max_major = info.get("max_major")
                            if max_major is not None and max_major > major:
                                candidate = _highest_exact_digest_in_major(
                                    info, max_major
                                )
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
                            if not already_current:
                                cross_major = (
                                    target_major is not None
                                    and major is not None
                                    and target_major != major
                                )
                                if cross_major:
                                    detail_desc = (
                                        f"cross-major SHA re-pin to {tag} "
                                        f"(v{major} -> v{target_major}, {new_sha[:12]})"
                                    )
                                else:
                                    detail_desc = (
                                        f"refresh SHA digest to {tag} within v{major} "
                                        f"({new_sha[:12]})"
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
                                    comment_rewrite: tuple[str, str] | None = None
                                    if cross_major and uses.comment:
                                        # Rewrite the trailing # vN hint to the new major.
                                        comment_rewrite = (
                                            f"# v{major}",
                                            f"# v{target_major}",
                                        )
                                    replacements.append(
                                        PinReplacement(
                                            action=action,
                                            pin=pin,
                                            latest=new_sha,
                                            line=uses.line or None,
                                            drop_comment=False,
                                            comment_rewrite=comment_rewrite,
                                        )
                                    )
                                continue
                        # Unresolvable major / no newer digest → report only below.
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

                info = action_tag_info(action, cache=_cache)
                resolution = _resolve_sha_target(
                    pin,
                    comment_hint=uses.comment,
                    info=info,
                    tag_style=tag_style,
                )
                if resolution is None:
                    # Cannot resolve: keep reporting, do not rewrite.
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=pin,
                            latest=pin,
                            status="ok",
                            detail="SHA-pinned; report only (unresolvable: no tag maps to this SHA)",
                        )
                    )
                    continue

                target_tag, detail = resolution
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=target_tag,
                        status="ok",
                        detail=detail,
                    )
                )
                if apply:
                    replacements.append(
                        PinReplacement(
                            action=action,
                            pin=pin,
                            latest=target_tag,
                            line=uses.line or None,
                            drop_comment=False,  # preserve the # vN audit hint
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

            bare_major_exists = M in info["bare_majors"]
            max_major = info["max_major"]

            # In latest mode, a newer major upstream is rewritten (not just
            # reported), provided it can be safely targeted (#106).
            newer_major = max_major is not None and max_major > M
            latest_target = (
                _resolve_latest_target(info, tag_style)
                if upgrade_mode == "latest" and newer_major
                else None
            )

            if latest_target and latest_target != pin:
                bumps.append(
                    ActionBump(
                        workflow=rel,
                        action=action,
                        pin=pin,
                        latest=latest_target,
                        status="ok",
                        detail=f"cross-major {pin} -> {latest_target} (latest mode)",
                    )
                )
                if apply:
                    replacements.append(
                        PinReplacement(
                            action=action,
                            pin=pin,
                            latest=latest_target,
                            line=uses.line or None,
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
                if bare_major_exists:
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
                                line=uses.line or None,
                            )
                        )
                else:
                    # No bare major tag upstream — cannot collapse to floating.
                    # Advance the exact pin within its major instead (#115):
                    # e.g. astral-sh/setup-uv publishes only vN.M.P tags, so
                    # v8.1.0 would otherwise never move while v8.2.0 exists.
                    in_major = _highest_exact_in_major(info, M)
                    if in_major and in_major != pin:
                        detail = (
                            f"advance exact pin within v{M} "
                            "(no floating major tag upstream)"
                        )
                        if newer_major:
                            detail += f"; newer major v{max_major} available"
                        bumps.append(
                            ActionBump(
                                workflow=rel,
                                action=action,
                                pin=pin,
                                latest=in_major,
                                status="ok",
                                detail=detail,
                            )
                        )
                        if apply:
                            replacements.append(
                                PinReplacement(
                                    action=action,
                                    pin=pin,
                                    latest=in_major,
                                    line=uses.line or None,
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

        # Org allowed-actions policy check (report-only; never blocks pin bumps).
        if _org_policy is not None and _org_owner is not None:
            for uses in pins:
                action = uses.action
                if action.startswith("./"):
                    continue  # path-local: always allowed
                allowed_actions_mode = _org_policy.get("allowed_actions", "all")
                if allowed_actions_mode == "local_only":
                    violation = _action_policy_violation_local_only(
                        action, _org_policy, _org_owner
                    )
                else:
                    violation = _action_policy_violation(action, _org_policy)
                if violation:
                    bumps.append(
                        ActionBump(
                            workflow=rel,
                            action=action,
                            pin=uses.pin,
                            latest=uses.pin,
                            status="policy",
                            detail=violation,
                        )
                    )

    # Persist the actionlint cache (atomic write; best-effort).
    if _lint_cache is not None and _lint_cache_path is not None:
        _save_lint_cache(_lint_cache_path, _lint_cache)

    return bumps, len(workflows)
