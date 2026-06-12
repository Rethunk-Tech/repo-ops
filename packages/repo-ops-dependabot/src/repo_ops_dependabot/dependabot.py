#!/usr/bin/env python3
"""List or close Dependabot PRs that are already superseded by local manifests/lockfiles.

For each repo discovered under DEFAULT_ROOTS that has a github.com remote, list
open Dependabot PRs via ``gh pr list``.  For each PR, parse the bump (package,
old version, new version) from the title and check whether the repo's current
lockfiles/manifests already satisfy >= the target version.  If so, the PR is a
candidate to close.

Default mode: dry-run (print the "would-close" list).  Pass ``--close`` to
actually close PRs via ``gh pr close``.

Precision caveats
-----------------
- Lockfile version checks are text-search based (``name@version`` patterns).
  False negatives are possible when a package is present but the lockfile uses an
  unusual format or the package is not in the lockfile at all (workspace-hoisted).
- The title regex covers the common Dependabot title pattern
  ("Bump X from A to B" / "Bump X and Y from A to B").  Non-standard titles are
  treated as unparseable and included in the report but not closed.
- ``gh`` must be authenticated and on PATH; if not, all repos are skipped.

Console entry point: ``repo-ops dependabot`` (see ``--help`` for flags).
"""

from __future__ import annotations

import argparse
import contextlib
import json
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
from repo_ops_core.discovery import discover_repos
from repo_ops_core.git import repo_label, run_git
from repo_ops_lockfiles.parsers import (
    versions_bun_lock,
    versions_npm_lock,
    versions_pnpm_lock,
    versions_yarn_lock,
)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Parses: "Bump X from A to B" / "Bumps X from A to B"
# Also handles "Bump X and Y from A to B" (multi-package, groups bump)
_BUMP_RE = re.compile(
    r"^[Bb]umps?\s+(.+?)\s+from\s+(\S+)\s+to\s+(\S+)",
    re.IGNORECASE,
)

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
class DependabotPR:
    number: int
    title: str
    head_ref: str
    repo_path: str
    repo_label_str: str
    bump: BumpInfo | None = None
    superseded: bool | None = None  # None = unknown (unparseable / no lockfile)
    current_ver: str | None = None  # resolved from lockfile when superseded=True
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
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", remote_url)
    if m:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# gh subprocess calls
# ---------------------------------------------------------------------------

_GH_TIMEOUT = 30


def _gh_list_dependabot_prs(
    owner_repo: str,
) -> tuple[list[dict], str]:  # type: ignore[type-arg]
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
# Lockfile version resolution
# ---------------------------------------------------------------------------


def _lockfile_versions(repo: str) -> set[str]:
    """Return all name@version strings visible in lockfiles under *repo*.

    Precision note: this is a text-search across lockfile contents; it does not
    fully parse manifests.  Workspace-hoisted packages may not appear per-workspace.
    """
    root = Path(repo)
    versions: set[str] = set()

    for lockfile in root.rglob("package-lock.json"):
        with contextlib.suppress(Exception):
            versions |= versions_npm_lock(lockfile)

    for lockfile in root.rglob("yarn.lock"):
        with contextlib.suppress(Exception):
            versions |= versions_yarn_lock(lockfile)

    for lockfile in root.rglob("pnpm-lock.yaml"):
        with contextlib.suppress(Exception):
            versions |= versions_pnpm_lock(lockfile)

    for lockfile in root.rglob("bun.lock"):
        with contextlib.suppress(Exception):
            versions |= versions_bun_lock(lockfile)

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


def _check_superseded(
    pr: DependabotPR,
    lockfile_versions: set[str],
    manifest_versions: dict[str, str],
) -> tuple[bool | None, str | None]:
    """Return (superseded, current_version) for *pr*.

    Returns (None, None) when the PR title is unparseable or the package
    cannot be found in any lockfile or manifest.
    """
    if pr.bump is None:
        return None, None

    pkg = pr.bump.package
    target = _strip_v(pr.bump.to_ver)

    # Check Node lockfiles first (set of "name@version" strings)
    for entry in lockfile_versions:
        # entry might be "pkg@1.2.3" or "@scope/pkg@1.2.3"
        if entry.startswith(f"{pkg}@"):
            current = entry[len(pkg) + 1 :]
            if _version_gte(current, target):
                return True, current
            return False, current

    # Check manifest versions (go.mod, Cargo.lock)
    if pkg in manifest_versions:
        current = manifest_versions[pkg]
        if _version_gte(current, target):
            return True, current
        return False, current

    # Also handle short package names (last component of module path)
    short_pkg = pkg.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
    if short_pkg != pkg and short_pkg in manifest_versions:
        current = manifest_versions[short_pkg]
        if _version_gte(current, target):
            return True, current
        return False, current

    return None, None


# ---------------------------------------------------------------------------
# PR title parsing
# ---------------------------------------------------------------------------


def _parse_bump(title: str) -> BumpInfo | None:
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
        lockfile_vers = _lockfile_versions(repo)
        manifest_vers = _manifest_versions(repo)

        # Resolve the default branch name for close comments
        default_branch = "main"
        proc = run_git(
            repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", timeout=5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            default_branch = proc.stdout.strip().replace("origin/", "")

        for pr_dict in pr_dicts:
            pr = DependabotPR(
                number=int(pr_dict.get("number", 0)),
                title=pr_dict.get("title", ""),
                head_ref=pr_dict.get("headRefName", ""),
                repo_path=repo,
                repo_label_str=label,
            )
            pr.bump = _parse_bump(pr.title)
            pr.superseded, pr.current_ver = _check_superseded(
                pr, lockfile_vers, manifest_vers
            )
            rr.prs.append(pr)

        if do_close:
            for pr in rr.prs:
                if not pr.superseded:
                    continue
                pkg_name = pr.bump.package if pr.bump else "dependency"
                current_ver = pr.current_ver or "unknown"
                comment = (
                    f"Superseded by repo-ops: {pkg_name} is at {current_ver} "
                    f"on {default_branch}"
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
    superseded = sum(1 for r in results for pr in r.prs if pr.superseded is True)
    not_superseded = sum(1 for r in results for pr in r.prs if pr.superseded is False)
    unknown = sum(1 for r in results for pr in r.prs if pr.superseded is None)

    print(
        f"Dependabot PR sweep across {len(results)} github.com repo(s); "
        f"{total_prs} open PR(s)"
    )
    print(
        f"  superseded: {superseded}, not yet: {not_superseded}, "
        f"unparseable/unknown: {unknown}"
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
            superseded_str = (
                "superseded"
                if pr.superseded is True
                else ("not yet" if pr.superseded is False else "unknown")
            )
            current_str = f" (current: {pr.current_ver})" if pr.current_ver else ""
            close_str = ""
            if do_close and pr.superseded:
                close_str = (
                    " → closed"
                    if not pr.close_error
                    else f" → close FAILED: {pr.close_error}"
                )
            print(
                f"  #{pr.number:5d}  [{superseded_str}]{current_str}  "
                f"{pr.title}{close_str}"
            )
        print()

    if superseded > 0 and not do_close:
        print(f"{action_verb} {superseded} PR(s).  Re-run with --close to close them.")


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

precision caveats:
  Version checks are text-search based against lockfiles/manifests.
  False negatives possible for workspace-hoisted packages or non-standard
  lockfile formats.  Review the "would-close" list before --close.
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
    add_json_argument(parser, dest="json_out")
    add_verbose_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_dependabot_parser()
    args = parser.parse_args(argv)
    wire_verbose(args)

    roots = resolve_scan_roots(list(args.roots), parser)
    follow_symlinks = bool(getattr(args, "follow_symlinks", False))
    do_close = bool(args.close)

    results = scan_repos(roots, follow_symlinks=follow_symlinks, do_close=do_close)

    if args.json_out:
        payload: dict = {
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
                            "package": pr.bump.package if pr.bump else None,
                            "from_ver": pr.bump.from_ver if pr.bump else None,
                            "to_ver": pr.bump.to_ver if pr.bump else None,
                            "superseded": pr.superseded,
                            "current_ver": pr.current_ver,
                            "closed": (
                                not pr.close_error
                                if (do_close and pr.superseded)
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
                    1 for r in results for pr in r.prs if pr.superseded is True
                ),
                "not_yet": sum(
                    1 for r in results for pr in r.prs if pr.superseded is False
                ),
                "unknown": sum(
                    1 for r in results for pr in r.prs if pr.superseded is None
                ),
            },
        }
        print(json.dumps(payload, indent=None if args.compact else 2))
        return 0

    print_dependabot_report(results, do_close=do_close)
    return 0


if __name__ == "__main__":
    sys.exit(main())
