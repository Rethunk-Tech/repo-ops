"""Branch-and-PR flow after post-apply commits (opt-in via --pr).

Pure functions only — side effects isolated to run_pr_flow() which is
invoked from run.py after the commit phase. All subprocess calls are
routed through _run_gh() / run_git() so tests can patch a single target.

Idempotent: if the branch or an open PR already exists, the function
no-ops gracefully rather than erroring.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from repo_ops_core.git import run_git
from repo_ops_models import RepoBundle

DEFAULT_BRANCH_PREFIX = "repo-ops/deps"


@dataclass
class PROutcome:
    git_repo: Path
    label: str
    status: Literal["opened", "exists", "skipped", "failed"]
    branch: str | None = None
    pr_url: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Branch name helpers
# ---------------------------------------------------------------------------


def compute_branch_name(label: str, *, prefix: str = DEFAULT_BRANCH_PREFIX) -> str:
    """Deterministic, safe branch name from a bundle label.

    Strips leading home-dir markers, replaces non-alphanumeric/dash chars with
    dashes, collapses runs, and truncates to 60 chars so refs stay readable.

    Examples::

        compute_branch_name("org/my-repo")       → "repo-ops/deps/org-my-repo"
        compute_branch_name("~/src/some-thing")  → "repo-ops/deps/src-some-thing"
        compute_branch_name("plain-repo")         → "repo-ops/deps/plain-repo"
    """
    slug = label.lstrip("~/")
    slug = re.sub(r"[^a-zA-Z0-9\-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    slug = slug[:60]
    if not slug:
        slug = "unnamed"
    return f"{prefix}/{slug}"


# ---------------------------------------------------------------------------
# Subprocess helpers (isolated for mocking)
# ---------------------------------------------------------------------------


def _run_gh(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    """Run a gh CLI invocation.

    All gh calls in this module go through here so tests can patch a single
    target: ``repo_ops_upgrade.pr._run_gh``.
    """
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def gh_available() -> bool:
    """True if the gh CLI is installed and responds to --version."""
    try:
        proc = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Git branch operations
# ---------------------------------------------------------------------------


def branch_exists_local(repo: Path, branch: str) -> bool:
    """True if *branch* exists as a local ref."""
    proc = run_git(repo, "rev-parse", "--verify", f"refs/heads/{branch}", timeout=10)
    return proc.returncode == 0


def create_or_checkout_branch(repo: Path, branch: str) -> tuple[bool, str | None]:
    """Create branch at HEAD (or checkout if it already exists).

    Returns ``(ok, err_msg)``.  When the branch already exists locally the
    caller still ends up on it — push + PR-existence check handle idempotency
    for the remote/PR side.
    """
    if branch_exists_local(repo, branch):
        proc = run_git(repo, "checkout", branch, timeout=30)
    else:
        proc = run_git(repo, "checkout", "-b", branch, timeout=30)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git checkout failed").strip()
        return False, err
    return True, None


def push_branch(
    repo: Path, branch: str, remote: str = "origin"
) -> tuple[bool, str | None]:
    """Push *branch* to *remote* with ``--set-upstream``.

    Returns ``(ok, err_msg)``.  The push is always forced only if the remote
    branch already has the same content (``--force-with-lease`` is not used
    here to stay conservative; callers relying on idempotency should check
    branch existence first).
    """
    proc = run_git(repo, "push", "--set-upstream", remote, branch, timeout=120)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git push failed").strip()
        return False, err
    return True, None


# ---------------------------------------------------------------------------
# gh PR operations
# ---------------------------------------------------------------------------


def gh_pr_exists(repo: Path, branch: str) -> tuple[bool, str | None]:
    """Check for an open PR targeting *branch*.

    Returns ``(exists, url_or_None)``.  A non-zero gh exit code (no open PR,
    or gh not authenticated) is treated as "does not exist" — the open_pr step
    will surface any auth error with a clear message.
    """
    proc = _run_gh(
        ["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"],
        cwd=repo,
        timeout=30,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return True, proc.stdout.strip()
    return False, None


def detect_default_branch(repo: Path) -> str:
    """Detect the repo's default branch.

    Tries gh repo view first (most reliable), then falls back to parsing
    ``origin/HEAD``, then defaults to ``"main"``.
    """
    proc = _run_gh(
        [
            "gh",
            "repo",
            "view",
            "--json",
            "defaultBranchRef",
            "--jq",
            ".defaultBranchRef.name",
        ],
        cwd=repo,
        timeout=30,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    proc2 = run_git(repo, "rev-parse", "--abbrev-ref", "origin/HEAD", timeout=10)
    if proc2.returncode == 0:
        ref = proc2.stdout.strip()
        if ref.startswith("origin/"):
            return ref[len("origin/") :]
    return "main"


def open_or_update_pr(
    repo: Path,
    branch: str,
    *,
    base: str,
    title: str,
    body: str,
) -> tuple[Literal["opened", "exists", "failed"], str | None]:
    """Open a PR; no-op if one already exists.

    Returns ``(status, url_or_err_msg)``.
    """
    exists, url = gh_pr_exists(repo, branch)
    if exists:
        return "exists", url

    proc = _run_gh(
        [
            "gh",
            "pr",
            "create",
            "--head",
            branch,
            "--base",
            base,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=repo,
        timeout=60,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "gh pr create failed").strip()
        return "failed", err
    return "opened", proc.stdout.strip() or None


# ---------------------------------------------------------------------------
# PR content builder
# ---------------------------------------------------------------------------


def _build_pr_content(bundle: RepoBundle, *, branch: str) -> tuple[str, str]:
    """Derive PR title and body from the bundle's applied changes."""
    from repo_ops_upgrade.post_apply_commit import classify_commit_subject

    # Build a representative file list so classify_commit_subject can pick the right
    # subject — one canonical filename per manager that produced changes this run.
    files_hint: list[str] = []
    for wr in bundle.workspaces:
        if not (wr.changes or wr.remediations):
            continue
        m = wr.workspace.manager
        if m in ("yarn", "npm", "pnpm", "bun"):
            files_hint.append("package.json")
        elif m in ("uv", "hatch", "pip-tools", "poetry"):
            files_hint.append("pyproject.toml")
        elif m == "go":
            files_hint.append("go.mod")
        elif m == "rust":
            files_hint.append("Cargo.toml")

    for ab in bundle.actions:
        if ab.status == "ok" and ab.pin != ab.latest:
            files_hint.append(".github/workflows/ci.yml")

    title = (
        classify_commit_subject(files_hint)
        if files_hint
        else "chore(deps): apply repo-ops dependency updates"
    )

    body_lines = [
        f"Automated dependency update for **{bundle.label}** via repo-ops.",
        "",
        "### Changes",
    ]
    for wr in bundle.workspaces:
        if wr.changes:
            ws = wr.workspace
            body_lines.append(
                f"- **{ws.label}** (`{ws.ecosystem}/{ws.manager}`): {len(wr.changes)} bump(s)"
            )
    n_actions = sum(
        1 for ab in bundle.actions if ab.status == "ok" and ab.pin != ab.latest
    )
    if n_actions:
        body_lines.append(f"- GitHub Actions: {n_actions} pin(s) bumped")

    body_lines += [
        "",
        f"Branch: `{branch}`",
        "",
        "_Created by [repo-ops](https://github.com/Rethunk-Tech/repo-ops)._",
    ]
    return title, "\n".join(body_lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_pr_flow(
    repo: Path,
    bundle: RepoBundle,
    *,
    branch_prefix: str = DEFAULT_BRANCH_PREFIX,
    base_branch: str | None = None,
) -> PROutcome:
    """After commit phase: create branch, push, open PR. Idempotent.

    If gh is not installed / not authenticated, returns a ``failed`` outcome
    with a clear error message — the caller marks the repo failed but the
    batch continues so other repos are not blocked.

    Steps
    -----
    1. Compute deterministic branch name.
    2. Check gh availability (fail fast with helpful message if missing).
    3. ``git checkout -b {branch}`` (or ``git checkout {branch}`` if exists).
    4. ``git push --set-upstream origin {branch}``.
    5. Detect base branch from gh / git / fallback.
    6. ``gh pr create`` (idempotent: checks for existing open PR first).
    """
    label = bundle.label
    branch = compute_branch_name(label, prefix=branch_prefix)

    if not gh_available():
        return PROutcome(
            repo,
            label,
            "failed",
            branch=branch,
            detail=(
                "gh CLI not found or not executable. "
                "Install gh (https://cli.github.com) and run 'gh auth login'."
            ),
        )

    ok, err = create_or_checkout_branch(repo, branch)
    if not ok:
        return PROutcome(repo, label, "failed", branch=branch, detail=f"branch: {err}")

    ok, err = push_branch(repo, branch)
    if not ok:
        return PROutcome(repo, label, "failed", branch=branch, detail=f"push: {err}")

    resolved_base = base_branch or detect_default_branch(repo)
    title, body = _build_pr_content(bundle, branch=branch)

    pr_status, url_or_err = open_or_update_pr(
        repo, branch, base=resolved_base, title=title, body=body
    )
    if pr_status == "failed":
        return PROutcome(repo, label, "failed", branch=branch, detail=url_or_err)

    return PROutcome(
        repo,
        label,
        pr_status,
        branch=branch,
        pr_url=url_or_err if pr_status in ("opened", "exists") else None,
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_pr_outcome(outcome: PROutcome) -> None:
    """Print a single-line PR outcome summary."""
    prefix = outcome.label
    if outcome.status == "opened":
        url_part = f" {outcome.pr_url}" if outcome.pr_url else f" ({outcome.branch})"
        print(f"[pr] {prefix}: opened{url_part}")
    elif outcome.status == "exists":
        url_part = f" {outcome.pr_url}" if outcome.pr_url else f" ({outcome.branch})"
        print(f"[pr] {prefix}: PR already exists{url_part}")
    elif outcome.status == "skipped":
        print(f"[pr skip] {prefix}: {outcome.detail}")
    elif outcome.status == "failed":
        print(f"[pr fail] {prefix}: {outcome.detail}", file=sys.stderr)
