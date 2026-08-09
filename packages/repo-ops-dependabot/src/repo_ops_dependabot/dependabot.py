#!/usr/bin/env python3
"""List or close Dependabot PRs that are already superseded by local manifests/lockfiles.

For each repo discovered under DEFAULT_ROOTS that has a github.com remote, list
open Dependabot PRs via ``gh pr list``.  For each PR, parse the bump (package,
old version, new version) from the title and check whether the repo's current
lockfiles/manifests already satisfy >= the target version.  If so, the PR is a
candidate to close.

Default mode: dry-run (print the "would-close" list).  Pass ``--close`` to
actually close PRs via ``gh pr close``.

Title patterns handled
----------------------
- ``Bump X from A to B``  (standard Dependabot single-package)
- ``Bumps X from A to B``  (grammatical variant)
- ``bump X from A to B``  (lowercase)
- ``bump X from A to B in /path``  (monorepo sub-path)
- ``chore(deps): bump X from A to B``  (conventional-commit prefix)
- ``build(deps-dev): bump X from A to B``  (build-scope prefix)
- ``[Security] Bump X from A to B``  (security advisory; ``is_security=True``)
- ``Bump the <group-name> group with N updates``  (grouped PR; body parsed)

Grouped PRs
-----------
When the title matches the group pattern, the PR body is fetched via
``gh pr view --json body``.  Each row of the Markdown update table is parsed to
extract (package, from_version, to_version).  A grouped PR is eligible for
auto-close **only** when **all** rows are satisfied with high-confidence
lockfile/manifest matches.  Body parse failures produce ``is_unparseable=True``
and the PR is never auto-closed.

Satisfaction precision
----------------------
- **High-confidence**: resolved version read from a lockfile parser (npm-lock,
  yarn-lock, pnpm-lock, bun-lock, uv-lock, poetry.lock, pdm.lock, Pipfile.lock)
  or from go.mod / Cargo.lock.
  Auto-close eligible when ``version >= target``.
- **Low-confidence** (``low_confidence=True``): text-search fallback used when no
  lockfile parser returned a result.  These matches are **never** auto-closed
  regardless of ``--close``.

Precision caveats
-----------------
- The lockfile parsers are the same ones used by the upgrade subcommand.  They
  may miss workspace-hoisted packages.
- ``gh`` must be authenticated and on PATH; if not, all repos are skipped.

Console entry point: ``repo-ops dependabot`` (see ``--help`` for flags).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from repo_ops_core.cli import (
    add_follow_symlinks_argument,
    add_json_argument,
    add_roots_argument,
    add_verbose_argument,
    resolve_scan_roots,
    wire_verbose,
)
from repo_ops_core.constants import SKIP_DIRS
from repo_ops_core.discovery import discover_repos
from repo_ops_core.git import repo_label, run_git
from repo_ops_lockfiles.parsers import (
    versions_bun_lock,
    versions_npm_lock,
    versions_pdm_lock,
    versions_pipenv_lock,
    versions_pnpm_lock,
    versions_uv_lock,
    versions_yarn_lock,
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Conventional-commit prefixes (chore(deps): / build(deps-dev): / etc.)
_CC_PREFIX = r"(?:[a-zA-Z]+(?:\([^)]*\))?:\s*)?"

# [Security] prefix
_SEC_PREFIX = r"(?:\[Security\]\s*)?"

# Core bump pattern: "Bump(s) X from A to B [in /path]"
_BUMP_RE = re.compile(
    r"^"
    + _CC_PREFIX
    + _SEC_PREFIX
    + r"[Bb]umps?\s+(.+?)\s+from\s+(\S+)\s+to\s+(\S+)"
    + r"(?:\s+in\s+\S+)?",
    re.IGNORECASE,
)

# Grouped PR: "Bump the <group-name> group with N updates"
_GROUP_RE = re.compile(
    r"^" + _CC_PREFIX + r"[Bb]ump\s+the\s+(.+?)\s+group\s+with\s+(\d+)\s+update",
    re.IGNORECASE,
)

# Detect [Security] prefix
_SEC_RE = re.compile(r"^\[Security\]", re.IGNORECASE)

# Normalise version strings: strip leading "v"
_VER_RE = re.compile(r"^v?(\d.*)$")


def _strip_v(ver: str) -> str:
    m = _VER_RE.match(ver.strip())
    return m.group(1) if m else ver.strip()


@dataclass
class BumpInfo:
    package: str
    from_ver: str
    to_ver: str


@dataclass
class GroupRow:
    """Single row from a grouped PR update table."""

    package: str
    from_ver: str
    to_ver: str
    superseded: bool | None = None  # None = unknown
    current_ver: str | None = None
    low_confidence: bool = False


@dataclass
class DependabotPR:
    number: int
    title: str
    head_ref: str
    repo_path: str
    repo_label_str: str
    bump: BumpInfo | None = None
    # Grouped PR fields
    is_grouped: bool = False
    group_rows: list[GroupRow] = field(default_factory=list)
    # Security advisory flag
    is_security: bool = False
    # Supersession result (single-package PRs)
    superseded: bool | None = None  # None = unknown (unparseable / no lockfile)
    current_ver: str | None = None  # resolved from lockfile when superseded=True
    low_confidence: bool = False  # True = text-search fallback, never auto-close
    # Parse / body-fetch failure
    is_unparseable: bool = False
    unparseable_reason: str = ""
    close_error: str = ""


@dataclass
class RepoResult:
    label: str
    path: str
    remote: str
    prs: list[DependabotPR] = field(default_factory=list)
    gh_error: str = ""


# ---------------------------------------------------------------------------
# Git remote detection
# ---------------------------------------------------------------------------


def _github_remote(repo: str) -> str | None:
    """Return the first github.com remote URL found, or None."""
    proc = run_git(repo, "remote", "-v", timeout=10)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        url = parts[1]
        if "github.com" in url:
            return url
    return None


def _owner_repo(remote_url: str) -> str | None:
    """Parse 'owner/repo' from a github.com remote URL."""
    # SSH: git@github.com:owner/repo.git
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?/?$", remote_url)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# gh subprocess calls
# ---------------------------------------------------------------------------

_GH_TIMEOUT = 30


def _gh_list_dependabot_prs(
    owner_repo: str,
) -> tuple[list[dict[str, str | int]], str]:
    """Return (list_of_pr_dicts, error_string)."""
    if shutil.which("gh") is None:
        return [], "gh not on PATH"
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                owner_repo,
                "--author",
                "app/dependabot",
                "--state",
                "open",
                "--json",
                "number,title,headRefName",
            ],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [], f"gh pr list timed out after {_GH_TIMEOUT}s"
    except FileNotFoundError:
        return [], "gh not found"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "gh pr list failed").strip()[:200]
        return [], err
    try:
        data = json.loads(proc.stdout)
        if not isinstance(data, list):
            return [], "unexpected gh output (not a list)"
        return data, ""
    except (json.JSONDecodeError, ValueError) as exc:
        return [], f"gh output parse error: {exc}"


def _gh_pr_body(owner_repo: str, pr_number: int) -> tuple[str, str]:
    """Return (body_text, error_string) for the given PR."""
    if shutil.which("gh") is None:
        return "", "gh not on PATH"
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                owner_repo,
                "--json",
                "body",
            ],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "", f"gh pr view timed out after {_GH_TIMEOUT}s"
    except FileNotFoundError:
        return "", "gh not found"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "gh pr view failed").strip()[:200]
        return "", err
    try:
        data = json.loads(proc.stdout)
        return str(data.get("body") or ""), ""
    except (json.JSONDecodeError, ValueError) as exc:
        return "", f"gh pr view parse error: {exc}"


def _gh_close_pr(owner_repo: str, pr_number: int, comment: str) -> str:
    """Close *pr_number* with *comment*; return error string (empty = success)."""
    if shutil.which("gh") is None:
        return "gh not on PATH"
    try:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "close",
                str(pr_number),
                "--repo",
                owner_repo,
                "--comment",
                comment,
            ],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"timeout after {_GH_TIMEOUT}s"
    except FileNotFoundError:
        return "gh not found"
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "gh pr close failed").strip()[:200]
    return ""


# ---------------------------------------------------------------------------
# Lockfile version resolution (high-confidence via parser)
# ---------------------------------------------------------------------------


def _lockfile_resolved(repo: str) -> dict[str, str]:
    """Return {name: version} of all packages resolved in lockfiles under *repo*.

    Uses the same lockfile parsers as the upgrade subcommand.  The returned dict
    maps package name (lowercased) to the highest resolved version seen.  When
    multiple lockfiles exist (monorepo), the highest semver wins.

    Confidence: **high** — parsed directly from the lockfile structure.
    """
    root = Path(repo)
    resolved: dict[str, str] = {}

    def _update(name: str, ver: str) -> None:
        key = name.lower()
        if key not in resolved or _version_gte(ver, resolved[key]):
            resolved[key] = ver

    for lockfile in root.rglob("package-lock.json"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_npm_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("yarn.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_yarn_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("pnpm-lock.yaml"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_pnpm_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("bun.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_bun_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("uv.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_uv_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("poetry.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_uv_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("pdm.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_pdm_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    for lockfile in root.rglob("Pipfile.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            for entry in versions_pipenv_lock(lockfile):
                if "@" in entry:
                    pkg, ver = _split_name_ver(entry)
                    if pkg:
                        _update(pkg, ver)

    return resolved


def _split_name_ver(entry: str) -> tuple[str, str]:
    """Split a ``name@version`` string into (name, version).

    Handles scoped packages like ``@babel/core@7.22.0``.
    """
    if entry.startswith("@"):
        # @scope/pkg@version
        second_at = entry.find("@", 1)
        if second_at == -1:
            return entry, ""
        return entry[:second_at], entry[second_at + 1 :]
    at = entry.find("@")
    if at == -1:
        return entry, ""
    return entry[:at], entry[at + 1 :]


def _lockfile_versions(repo: str) -> set[str]:
    """Return all name@version strings visible in lockfiles under *repo*.

    Precision note: this is a text-search across lockfile contents; it does not
    fully parse manifests.  Workspace-hoisted packages may not appear per-workspace.
    """
    root = Path(repo)
    versions: set[str] = set()

    for lockfile in root.rglob("package-lock.json"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_npm_lock(lockfile)

    for lockfile in root.rglob("yarn.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_yarn_lock(lockfile)

    for lockfile in root.rglob("pnpm-lock.yaml"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_pnpm_lock(lockfile)

    for lockfile in root.rglob("bun.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_bun_lock(lockfile)

    for lockfile in root.rglob("uv.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_uv_lock(lockfile)

    for lockfile in root.rglob("poetry.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_uv_lock(lockfile)

    for lockfile in root.rglob("pdm.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_pdm_lock(lockfile)

    for lockfile in root.rglob("Pipfile.lock"):
        if any(part in SKIP_DIRS for part in lockfile.parts):
            continue
        with contextlib.suppress(Exception):
            versions |= versions_pipenv_lock(lockfile)

    return versions


def _manifest_versions(repo: str) -> dict[str, str]:
    """Scan go.mod, Cargo.toml, and pyproject.toml for declared versions.

    Returns {package_name: version} for packages explicitly declared in manifests.
    This is a best-effort extraction; complex constraints are not fully evaluated.
    """
    root = Path(repo)
    result: dict[str, str] = {}

    # Go: parse go.mod "require" lines
    for go_mod in root.rglob("go.mod"):
        with contextlib.suppress(Exception):
            text = go_mod.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"^\s+([^\s]+)\s+(v[^\s]+)", text, re.MULTILINE):
                result[m.group(1)] = _strip_v(m.group(2))

    # Rust: parse Cargo.lock [[package]] blocks
    for cargo_lock in root.rglob("Cargo.lock"):
        with contextlib.suppress(Exception):
            text = cargo_lock.read_text(encoding="utf-8", errors="replace")
            pkg_blocks = re.split(r"\[\[package\]\]", text)
            for block in pkg_blocks[1:]:
                name_m = re.search(r'name\s*=\s*"([^"]+)"', block)
                ver_m = re.search(r'version\s*=\s*"([^"]+)"', block)
                if name_m and ver_m:
                    result[name_m.group(1)] = ver_m.group(1)

    return result


def _version_gte(current: str, target: str) -> bool:
    """Return True when *current* >= *target* using tuple comparison."""

    def _to_tuple(v: str) -> tuple[int, ...]:
        parts = _strip_v(v).split(".")
        nums: list[int] = []
        for p in parts[:4]:  # cap at 4 segments
            try:
                nums.append(int(re.sub(r"[^0-9].*", "", p)))
            except ValueError:
                break
        return tuple(nums) if nums else (0,)

    return _to_tuple(current) >= _to_tuple(target)


def _resolve_package_version(
    pkg: str,
    lockfile_resolved: dict[str, str],
    lockfile_versions: set[str],
    manifest_versions: dict[str, str],
) -> tuple[str | None, bool]:
    """Return (current_version, is_high_confidence) for *pkg*, or (None, False).

    Lookup order:
    1. High-confidence: lockfile_resolved (parsed by lockfile parsers).
    2. High-confidence: manifest_versions (go.mod / Cargo.lock).
    3. Low-confidence: text-search lockfile_versions set.
    """
    pkg_lower = pkg.lower()

    # 1. Lockfile parser result (high confidence)
    if pkg_lower in lockfile_resolved:
        return lockfile_resolved[pkg_lower], True

    # PEP 503 normalization for Python packages: uv.lock stores names
    # lowercased with hyphens, while PR titles may use underscores/dots.
    pep503_pkg = re.sub(r"[-_.]+", "-", pkg_lower)
    if pep503_pkg != pkg_lower and pep503_pkg in lockfile_resolved:
        return lockfile_resolved[pep503_pkg], True

    # Also try short-name match for Go module paths
    short_pkg = pkg.rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
    if short_pkg != pkg_lower and short_pkg in lockfile_resolved:
        return lockfile_resolved[short_pkg], True

    # 2. Manifest versions (go.mod, Cargo.lock) — high confidence
    if pkg in manifest_versions:
        return manifest_versions[pkg], True
    if short_pkg != pkg and short_pkg in manifest_versions:
        return manifest_versions[short_pkg], True

    # 3. Text-search lockfile_versions (low confidence)
    for entry in lockfile_versions:
        if entry.startswith(f"{pkg}@"):
            current = entry[len(pkg) + 1 :]
            return current, False
        if entry.lower().startswith(f"{pkg_lower}@"):
            current = entry[len(pkg_lower) + 1 :]
            return current, False

    return None, False


def _check_superseded(
    pr: DependabotPR,
    lockfile_versions: set[str],
    manifest_versions: dict[str, str],
    lockfile_resolved: dict[str, str] | None = None,
) -> tuple[bool | None, str | None]:
    """Return (superseded, current_version) for *pr*.

    Returns (None, None) when the PR title is unparseable or the package
    cannot be found in any lockfile or manifest.

    When *lockfile_resolved* is provided, high-confidence parser results are
    preferred over text-search.  Sets ``pr.low_confidence`` when the match came
    from text-search only.
    """
    if pr.bump is None:
        return None, None

    pkg = pr.bump.package
    target = _strip_v(pr.bump.to_ver)
    resolved = lockfile_resolved or {}

    current_ver, high_conf = _resolve_package_version(
        pkg, resolved, lockfile_versions, manifest_versions
    )
    if current_ver is None:
        return None, None

    pr.low_confidence = not high_conf
    if _version_gte(current_ver, target):
        return True, current_ver
    return False, current_ver


# ---------------------------------------------------------------------------
# PR title parsing
# ---------------------------------------------------------------------------


def _is_security_title(title: str) -> bool:
    """Return True when the title has a [Security] prefix."""
    return bool(_SEC_RE.match(title.strip()))


def _parse_bump(title: str) -> BumpInfo | None:
    """Parse a single-package bump title.  Returns None for group/unparseable."""
    # Reject group titles early
    if _GROUP_RE.match(title.strip()):
        return None
    m = _BUMP_RE.match(title.strip())
    if not m:
        return None
    raw_pkg = m.group(1).strip()
    # Multi-package titles: "X and Y" — take first package name
    first_pkg = re.split(r"\s+and\s+", raw_pkg, maxsplit=1)[0].strip()
    return BumpInfo(
        package=first_pkg,
        from_ver=m.group(2).strip(),
        to_ver=m.group(3).strip(),
    )


# Markdown update table row patterns (grouped PR body).
# Two variants seen in practice:
#   | package-name | from `A` | to `B` |
#   | package-name | `A` → `B` |
_TABLE_ROW_RE = re.compile(
    r"^\|\s*`?([^`|]+?)`?\s*\|"  # package name cell
    r"\s*`?([^`|\n→]+?)`?\s*"  # from-version or combined cell
    r"(?:(?:→|->|to)\s*`?([^`|\n]+?)`?\s*)?"  # optional to-version
    r"\|",
    re.IGNORECASE,
)

# Alternative: explicit "from X to Y" in table cells
_TABLE_FROM_TO_RE = re.compile(
    r"^\|\s*`?([^`|]+?)`?\s*\|"  # package
    r"\s*`?([^`|]+?)`?\s*\|"  # from
    r"\s*`?([^`|]+?)`?\s*\|",  # to
    re.IGNORECASE,
)


def _parse_group_body(body: str) -> list[GroupRow] | None:
    """Parse the update table from a grouped Dependabot PR body.

    Returns None when the body does not contain a recognisable update table.
    Returns an empty list when the table was found but had no parseable rows
    (caller treats this as unparseable).
    """
    rows: list[GroupRow] = []
    in_table = False

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table and rows:
                # Table ended; we have our rows
                break
            continue

        # Skip separator rows (|---|---|)
        if re.match(r"^\|[\s\-|:]+\|$", stripped):
            in_table = True
            continue

        in_table = True

        # Try 3-cell format: | pkg | from | to |
        m3 = _TABLE_FROM_TO_RE.match(stripped)
        if m3:
            pkg = m3.group(1).strip().strip("`").strip()
            frm = m3.group(2).strip().strip("`").strip()
            to_ = m3.group(3).strip().strip("`").strip()
            if (
                pkg
                and frm
                and to_
                and not re.match(r"package|dependency|name", pkg, re.I)
            ):
                rows.append(GroupRow(package=pkg, from_ver=frm, to_ver=to_))
            continue

        # Try 2-cell format: | pkg | from → to |
        m2 = _TABLE_ROW_RE.match(stripped)
        if m2:
            pkg = m2.group(1).strip().strip("`").strip()
            frm = m2.group(2).strip().strip("`").strip()
            to_ = (m2.group(3) or "").strip().strip("`").strip()
            if (
                pkg
                and frm
                and to_
                and not re.match(r"package|dependency|name", pkg, re.I)
            ):
                rows.append(GroupRow(package=pkg, from_ver=frm, to_ver=to_))

    if not in_table:
        return None
    return rows


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------


def scan_repos(
    roots: list[str],
    *,
    follow_symlinks: bool = False,
    do_close: bool = False,
    default_branch_comment: bool = True,
) -> list[RepoResult]:
    """Discover repos, list Dependabot PRs, check supersession."""
    common_to_repo = discover_repos(roots, follow_symlinks=follow_symlinks)
    results: list[RepoResult] = []

    for repo in sorted(common_to_repo.values()):
        remote_url = _github_remote(repo)
        if not remote_url:
            continue  # not a github.com repo
        owner_repo_str = _owner_repo(remote_url)
        if not owner_repo_str:
            continue

        label = repo_label(repo, roots)
        rr = RepoResult(label=label, path=repo, remote=owner_repo_str)
        results.append(rr)

        pr_dicts, gh_err = _gh_list_dependabot_prs(owner_repo_str)
        if gh_err:
            rr.gh_error = gh_err
            continue

        # Pre-compute lockfile/manifest versions for this repo once
        lockfile_resolved_map = _lockfile_resolved(repo)
        lockfile_vers = _lockfile_versions(repo)
        manifest_vers = _manifest_versions(repo)

        # Resolve the default branch name for close comments.  None when
        # resolution fails — never assume "main" (wrong on master/trunk repos).
        default_branch: str | None = None
        proc = run_git(
            repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", timeout=5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            default_branch = proc.stdout.strip().replace("origin/", "")

        for pr_dict in pr_dicts:
            pr = DependabotPR(
                number=int(str(pr_dict.get("number", 0))),
                title=str(pr_dict.get("title", "")),
                head_ref=str(pr_dict.get("headRefName", "")),
                repo_path=repo,
                repo_label_str=label,
            )
            pr.is_security = _is_security_title(pr.title)

            # Detect grouped PR
            group_m = _GROUP_RE.match(pr.title.strip())
            if group_m:
                pr.is_grouped = True
                body, body_err = _gh_pr_body(owner_repo_str, pr.number)
                if body_err:
                    pr.is_unparseable = True
                    pr.unparseable_reason = f"body fetch failed: {body_err}"
                else:
                    parsed_rows = _parse_group_body(body)
                    if parsed_rows is None or len(parsed_rows) == 0:
                        pr.is_unparseable = True
                        pr.unparseable_reason = (
                            "could not parse update table in PR body"
                        )
                    else:
                        pr.group_rows = parsed_rows
                        # Check each row
                        for row in pr.group_rows:
                            row_bump = BumpInfo(
                                package=row.package,
                                from_ver=row.from_ver,
                                to_ver=row.to_ver,
                            )
                            fake_pr = DependabotPR(
                                number=pr.number,
                                title=pr.title,
                                head_ref=pr.head_ref,
                                repo_path=repo,
                                repo_label_str=label,
                                bump=row_bump,
                            )
                            sup, cur = _check_superseded(
                                fake_pr,
                                lockfile_vers,
                                manifest_vers,
                                lockfile_resolved_map,
                            )
                            row.superseded = sup
                            row.current_ver = cur
                            row.low_confidence = fake_pr.low_confidence
                # Grouped PR: superseded only when ALL rows high-confidence satisfied
                if pr.is_unparseable or not pr.group_rows:
                    pr.superseded = None
                else:
                    all_ok = all(
                        row.superseded is True and not row.low_confidence
                        for row in pr.group_rows
                    )
                    pr.superseded = bool(all_ok)
            else:
                # Single-package PR
                pr.bump = _parse_bump(pr.title)
                if pr.bump is None:
                    pr.is_unparseable = True
                    pr.unparseable_reason = "title did not match any known bump pattern"
                    pr.superseded = None
                else:
                    pr.superseded, pr.current_ver = _check_superseded(
                        pr, lockfile_vers, manifest_vers, lockfile_resolved_map
                    )

            rr.prs.append(pr)

        if do_close:
            for pr in rr.prs:
                # Safety: never close unparseable, low-confidence, or not-superseded
                if pr.is_unparseable:
                    continue
                if not pr.superseded:
                    continue
                if pr.low_confidence:
                    continue
                pkg_name = pr.bump.package if pr.bump else "dependency group"
                current_ver = pr.current_ver or "unknown"
                branch_clause = (
                    f" on {default_branch}"
                    if default_branch
                    else " (could not resolve default branch)"
                )
                if pr.is_grouped:
                    comment = (
                        f"Superseded by repo-ops: all {len(pr.group_rows)} packages "
                        f"in this group are at target versions{branch_clause}"
                    )
                else:
                    comment = (
                        f"Superseded by repo-ops: {pkg_name} is at {current_ver}"
                        f"{branch_clause}"
                    )
                err = _gh_close_pr(owner_repo_str, pr.number, comment)
                pr.close_error = err

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_dependabot_report(results: list[RepoResult], *, do_close: bool) -> None:
    action_verb = "Closed" if do_close else "Would close"
    total_prs = sum(len(r.prs) for r in results)
    superseded = sum(
        1
        for r in results
        for pr in r.prs
        if pr.superseded is True and not pr.low_confidence
    )
    not_superseded = sum(1 for r in results for pr in r.prs if pr.superseded is False)
    unknown = sum(
        1 for r in results for pr in r.prs if pr.superseded is None or pr.is_unparseable
    )
    low_conf = sum(
        1
        for r in results
        for pr in r.prs
        if pr.superseded is True and pr.low_confidence
    )

    print(
        f"Dependabot PR sweep across {len(results)} github.com repo(s); "
        f"{total_prs} open PR(s)"
    )
    print(
        f"  superseded: {superseded}, not yet: {not_superseded}, "
        f"unparseable/unknown: {unknown}"
        + (f", low-confidence (report-only): {low_conf}" if low_conf else "")
    )
    print()

    for rr in results:
        if rr.gh_error:
            print(f"[{rr.label}] gh error: {rr.gh_error}")
            continue
        if not rr.prs:
            continue

        print(f"[{rr.label}]  ({rr.remote})")
        for pr in rr.prs:
            if pr.is_unparseable:
                superseded_str = f"unparseable: {pr.unparseable_reason}"
            elif pr.superseded is True and pr.low_confidence:
                superseded_str = "superseded (low-confidence)"
            elif pr.superseded is True:
                superseded_str = "superseded"
            elif pr.superseded is False:
                superseded_str = "not yet"
            else:
                superseded_str = "unknown"
            current_str = f" (current: {pr.current_ver})" if pr.current_ver else ""
            security_str = " [SECURITY]" if pr.is_security else ""
            grouped_str = " [grouped]" if pr.is_grouped else ""
            close_str = ""
            if (
                do_close
                and pr.superseded
                and not pr.low_confidence
                and not pr.is_unparseable
            ):
                close_str = (
                    " → closed"
                    if not pr.close_error
                    else f" → close FAILED: {pr.close_error}"
                )
            print(
                f"  #{pr.number:5d}  [{superseded_str}]{current_str}{security_str}"
                f"{grouped_str}  {pr.title}{close_str}"
            )
            if pr.is_grouped and pr.group_rows:
                for row in pr.group_rows:
                    row_sup = (
                        "ok"
                        if row.superseded
                        else ("pending" if row.superseded is False else "?")
                    )
                    lc_str = " [low-conf]" if row.low_confidence else ""
                    print(
                        f"           {row.package} {row.from_ver}→{row.to_ver}"
                        f" [{row_sup}{lc_str}]"
                    )
        print()

    if superseded > 0 and not do_close:
        print(f"{action_verb} {superseded} PR(s).  Re-run with --close to close them.")
    if low_conf > 0:
        print(
            f"Note: {low_conf} low-confidence match(es) reported but never auto-closed. "
            "Verify manually."
        )


# ---------------------------------------------------------------------------
# dependabot.yml scaffold / prune
# ---------------------------------------------------------------------------

# Indicator files that signal a given package-ecosystem is in use
_NODE_INDICATORS: frozenset[str] = frozenset(
    {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock"}
)
_PY_INDICATORS: frozenset[str] = frozenset(
    {
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile",
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "pdm.lock",
        "Pipfile.lock",
    }
)
_GO_INDICATORS: frozenset[str] = frozenset({"go.mod"})
_RUST_INDICATORS: frozenset[str] = frozenset({"Cargo.toml"})
_DOCKER_INDICATORS: frozenset[str] = frozenset({"Dockerfile", "docker-compose.yml"})


def _rel_dir(dp: Path, repo_root: Path) -> str:
    """Return dependabot.yml-style relative directory (leading slash, '/' = root)."""
    with contextlib.suppress(ValueError):
        rel = dp.relative_to(repo_root)
        rel_str = str(rel).replace(os.sep, "/")
        return "/" if rel_str == "." else f"/{rel_str}"
    return "/"


def detect_ecosystems(repo_path: Path) -> list[dict[str, str]]:
    """Return sorted list of ``{"package-ecosystem": ..., "directory": ...}`` dicts.

    Walks the repo tree and maps indicator files to dependabot ``package-ecosystem``
    values.  ``github-actions`` is detected via ``.github/workflows/*.yml``.
    """
    seen: set[tuple[str, str]] = set()
    results: list[dict[str, str]] = []

    def _add(eco: str, directory: str) -> None:
        key = (eco, directory)
        if key not in seen:
            seen.add(key)
            results.append({"package-ecosystem": eco, "directory": directory})

    # github-actions: always at "/"  (workflows live under .github/workflows)
    workflows_dir = repo_path / ".github" / "workflows"
    if workflows_dir.is_dir() and any(
        f.suffix in (".yml", ".yaml") for f in workflows_dir.iterdir() if f.is_file()
    ):
        _add("github-actions", "/")

    # Walk tree for other ecosystems (prune .github, node_modules, etc.)
    _walk_skip = SKIP_DIRS | {".github"}
    for dirpath_str, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in _walk_skip]
        dp = Path(dirpath_str)
        fname_set = set(filenames)
        rel = _rel_dir(dp, repo_path)

        if fname_set & _NODE_INDICATORS:
            _add("npm", rel)
        if fname_set & _PY_INDICATORS:
            _add("pip", rel)
        if fname_set & _GO_INDICATORS:
            _add("gomod", rel)
        if fname_set & _RUST_INDICATORS:
            _add("cargo", rel)
        if fname_set & _DOCKER_INDICATORS or any(
            f == "Dockerfile" or f.startswith("Dockerfile.") for f in fname_set
        ):
            _add("docker", rel)

    return sorted(results, key=lambda e: (e["package-ecosystem"], e["directory"]))


def generate_dependabot_yml(
    entries: list[dict[str, str]],
    *,
    schedule: str = "weekly",
) -> str:
    """Render ``entries`` as a ``dependabot.yml`` YAML string.

    Generates a clean file from scratch (prune-by-regeneration: only detected
    ecosystems appear in the output).
    """
    lines = [
        "# Generated by repo-ops dependabot --scaffold",
        "version: 2",
        "updates:",
    ]
    for entry in entries:
        eco = entry["package-ecosystem"]
        directory = entry["directory"]
        lines += [
            f'  - package-ecosystem: "{eco}"',
            f'    directory: "{directory}"',
            "    schedule:",
            f'      interval: "{schedule}"',
        ]
    return "\n".join(lines) + "\n"


def scaffold_repos(
    roots: list[str],
    *,
    follow_symlinks: bool = False,
    do_write: bool = False,
    schedule: str = "weekly",
) -> int:
    """Scaffold / update ``.github/dependabot.yml`` for every repo under *roots*.

    Default mode is dry-run (prints what *would* be written).  Pass
    ``do_write=True`` to actually write the file.

    Returns 0 on success.
    """
    common_to_repo = discover_repos(roots, follow_symlinks=follow_symlinks)
    if not common_to_repo:
        print("No git repositories found under the specified roots.")
        return 0

    for repo_path_str in sorted(common_to_repo.values()):
        repo = Path(repo_path_str)
        label = repo_label(repo_path_str, roots)
        entries = detect_ecosystems(repo)

        if not entries:
            print(f"[{label}] No supported ecosystems detected; skipping.")
            continue

        eco_list = ", ".join(e["package-ecosystem"] for e in entries)
        yml_content = generate_dependabot_yml(entries, schedule=schedule)
        yml_path = repo / ".github" / "dependabot.yml"

        if do_write:
            yml_path.parent.mkdir(parents=True, exist_ok=True)
            yml_path.write_text(yml_content, encoding="utf-8")
            print(f"[{label}] Detected: {eco_list}")
            print(f"[{label}] Written: {yml_path}")
        else:
            print(f"[{label}] Detected: {eco_list}")
            print(f"[{label}] Dry-run — {yml_path} would contain:")
            for line in yml_content.splitlines():
                print(f"    {line}")
            print(f"[{label}] Re-run with --scaffold --write to apply.")
        print()

    return 0


def build_dependabot_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-ops dependabot",
        description=(
            "List open Dependabot PRs and identify those superseded by the "
            "current lockfiles/manifests on the default branch."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
exit codes:
  0  success (including dry-run; use --json to machine-check counts)
  1  unexpected error (gh unavailable, etc.)

precision:
  High-confidence: lockfile parser (npm/yarn/pnpm/bun/uv.lock) or
  go.mod/Cargo.lock.
  Low-confidence: text-search fallback.  Low-confidence matches are NEVER
  auto-closed regardless of --close.

  Grouped PRs: closed only when ALL rows are high-confidence satisfied.
  Unparseable titles/bodies: always report-only, never closed.
""",
    )
    add_roots_argument(parser)
    add_follow_symlinks_argument(parser)
    parser.add_argument(
        "--close",
        action="store_true",
        help=(
            "Actually close superseded Dependabot PRs via gh pr close. "
            "Default: dry-run only (print the would-close list)."
        ),
    )
    # ---- Scaffold mode -------------------------------------------------------
    parser.add_argument(
        "--scaffold",
        action="store_true",
        help=(
            "Detect ecosystems in each discovered repo and generate (or regenerate) "
            ".github/dependabot.yml with the matching package-ecosystem entries. "
            "Default: dry-run only; pass --write to write the file. "
            "Mutually exclusive with PR scanning: --close and --json are invalid "
            "in combination with --scaffold."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "With --scaffold: actually write .github/dependabot.yml. "
            "Default: dry-run (print what would be written). Requires --scaffold."
        ),
    )
    parser.add_argument(
        "--schedule",
        default="weekly",
        metavar="INTERVAL",
        help=(
            "Schedule interval for generated dependabot.yml entries "
            "(daily/weekly/monthly). Default: weekly."
        ),
    )
    add_json_argument(parser, dest="json_out")
    add_verbose_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_dependabot_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)

    scaffold = bool(getattr(args, "scaffold", False))
    write_flag = bool(getattr(args, "write", False))
    json_flag = bool(getattr(args, "json_out", False))
    close_flag = bool(getattr(args, "close", False))

    if write_flag and not scaffold:
        parser.error("--write requires --scaffold")
    if scaffold and json_flag:
        parser.error("--json is not supported with --scaffold")
    if scaffold and close_flag:
        parser.error("--close is not supported with --scaffold (PR-scan mode only)")

    try:
        roots = resolve_scan_roots(list(args.roots), parser)
        follow_symlinks = bool(getattr(args, "follow_symlinks", False))

        # Scaffold mode: detect ecosystems and generate dependabot.yml
        if scaffold:
            return scaffold_repos(
                roots,
                follow_symlinks=follow_symlinks,
                do_write=write_flag,
                schedule=str(getattr(args, "schedule", "weekly")),
            )

        do_close = close_flag

        results = scan_repos(roots, follow_symlinks=follow_symlinks, do_close=do_close)
    except Exception as exc:
        print(f"repo-ops dependabot: unexpected error: {exc}", file=sys.stderr)
        return 1

    if args.json_out:
        payload: dict[str, object] = {
            "schema_version": 1,
            "roots": roots,
            "dry_run": not do_close,
            "repos": [
                {
                    "label": rr.label,
                    "path": rr.path,
                    "remote": rr.remote,
                    "gh_error": rr.gh_error or None,
                    "prs": [
                        {
                            "number": pr.number,
                            "title": pr.title,
                            "head_ref": pr.head_ref,
                            "is_grouped": pr.is_grouped,
                            "is_security": pr.is_security,
                            "is_unparseable": pr.is_unparseable,
                            "unparseable_reason": pr.unparseable_reason or None,
                            "package": pr.bump.package if pr.bump else None,
                            "from_ver": pr.bump.from_ver if pr.bump else None,
                            "to_ver": pr.bump.to_ver if pr.bump else None,
                            "superseded": pr.superseded,
                            "low_confidence": pr.low_confidence,
                            "current_ver": pr.current_ver,
                            "group_rows": (
                                [
                                    {
                                        "package": row.package,
                                        "from_ver": row.from_ver,
                                        "to_ver": row.to_ver,
                                        "superseded": row.superseded,
                                        "current_ver": row.current_ver,
                                        "low_confidence": row.low_confidence,
                                    }
                                    for row in pr.group_rows
                                ]
                                if pr.is_grouped
                                else None
                            ),
                            "closed": (
                                not pr.close_error
                                if (
                                    do_close
                                    and pr.superseded
                                    and not pr.low_confidence
                                    and not pr.is_unparseable
                                )
                                else None
                            ),
                            "close_error": pr.close_error or None,
                        }
                        for pr in rr.prs
                    ],
                }
                for rr in results
            ],
            "summary": {
                "repos": len(results),
                "total_prs": sum(len(r.prs) for r in results),
                "superseded": sum(
                    1
                    for r in results
                    for pr in r.prs
                    if pr.superseded is True and not pr.low_confidence
                ),
                "not_yet": sum(
                    1 for r in results for pr in r.prs if pr.superseded is False
                ),
                "unknown": sum(
                    1
                    for r in results
                    for pr in r.prs
                    if pr.superseded is None or pr.is_unparseable
                ),
                "low_confidence": sum(
                    1
                    for r in results
                    for pr in r.prs
                    if pr.superseded is True and pr.low_confidence
                ),
            },
        }
        print(json.dumps(payload, indent=None if args.compact else 2))
        return 0

    print_dependabot_report(results, do_close=do_close)
    return 0


if __name__ == "__main__":
    sys.exit(main())
