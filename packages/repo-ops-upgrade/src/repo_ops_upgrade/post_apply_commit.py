"""Optional post-apply git commits (conventional or pluggable commit-agent)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from repo_ops_core.git import run_git
from repo_ops_lockfiles.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops_models import RepoBundle

CommitMode = Literal["none", "conventional", "agent"]

_FORBIDDEN_BASENAMES = frozenset(
    {
        ".env",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
    }
)
_FORBIDDEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_FORBIDDEN_FRAGMENTS = (
    "/.env.",
    "/.env/",
    "/secrets/",
    "/credentials/",
    "service-account",
    "private_key",
)


@dataclass
class CommitPlan:
    git_repo: Path
    label: str
    message: str
    files: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


@dataclass
class CommitOutcome:
    git_repo: Path
    label: str
    status: Literal["committed", "dry_run", "skipped", "failed"]
    message: str | None = None
    files: list[str] = field(default_factory=list)
    detail: str | None = None


def is_forbidden_commit_path(path: str) -> bool:
    """True when path must never be staged for an automated deps commit."""
    norm = path.replace("\\", "/").strip()
    base = Path(norm).name
    lower_base = base.lower()
    if lower_base in _FORBIDDEN_BASENAMES or base in _FORBIDDEN_BASENAMES:
        return True
    if lower_base.startswith(".env"):
        return True
    if any(lower_base.endswith(s) for s in _FORBIDDEN_SUFFIXES):
        return True
    lower_norm = norm.lower()
    return any(frag in lower_norm for frag in _FORBIDDEN_FRAGMENTS)


def _lockfile_names_for_manager(manager: str) -> frozenset[str]:
    if manager in ("yarn", "npm", "pnpm", "bun"):
        return frozenset(NODE_LOCKFILES)
    if manager in ("uv", "hatch", "pip-tools", "poetry"):
        return frozenset(PY_LOCKFILES)
    if manager == "go":
        return frozenset(GO_LOCKFILES)
    if manager == "rust":
        return frozenset(RUST_LOCKFILES)
    return frozenset()


def upgrade_scoped_paths(bundle: RepoBundle, porcelain: list[str]) -> list[str]:
    """Porcelain paths that belong to this upgrade (not unrelated dirty files)."""
    wanted_names: set[str] = set()
    prefixes: set[str] = set()
    for wr in bundle.workspaces:
        if not (wr.changes or wr.remediations):
            continue
        wanted_names.update(_lockfile_names_for_manager(wr.workspace.manager))
        try:
            rel = wr.workspace.path.resolve().relative_to(bundle.git_repo.resolve())
        except ValueError:
            rel = Path(".")
        if rel != Path("."):
            prefixes.add(str(rel).replace("\\", "/"))
    for ab in bundle.actions:
        if ab.workflow:
            prefixes.add(ab.workflow.replace("\\", "/"))
    scoped: list[str] = []
    for path in porcelain:
        norm = path.replace("\\", "/")
        base = Path(norm).name
        if base in wanted_names:
            scoped.append(path)
            continue
        if norm.startswith(".github/workflows/"):
            scoped.append(path)
            continue
        for prefix in prefixes:
            if prefix == norm or norm.startswith(f"{prefix}/"):
                scoped.append(path)
                break
    return scoped


def _git_unquote(s: str) -> str:
    """Decode a git-quoted path string.

    Git wraps paths containing spaces, non-ASCII bytes, or special characters
    in double quotes with C-style escape sequences (``\\n``, ``\\t``, ``\\\\``,
    ``\\"``, ``\\NNN`` octal).  When the input is not quoted it is returned
    unchanged.  Octal escapes encode raw UTF-8 bytes, so they are accumulated
    into a ``bytearray`` and decoded as UTF-8 at the end — handling multi-byte
    sequences such as ``\\303\\251`` (``é``) correctly.
    """
    if not (s.startswith('"') and s.endswith('"')):
        return s
    inner = s[1:-1]
    buf = bytearray()
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch != "\\":
            buf.extend(ch.encode("utf-8"))
            i += 1
        else:
            i += 1
            esc = inner[i]
            if esc == "n":
                buf.append(ord("\n"))
            elif esc == "t":
                buf.append(ord("\t"))
            elif esc == "r":
                buf.append(ord("\r"))
            elif esc == "\\":
                buf.append(ord("\\"))
            elif esc == '"':
                buf.append(ord('"'))
            elif esc.isdigit():
                buf.append(int(inner[i : i + 3], 8))
                i += 2  # two extra digits consumed; loop adds one
            else:
                buf.append(ord("\\"))
                buf.extend(esc.encode("utf-8"))
            i += 1
    return buf.decode("utf-8")


def _parse_porcelain(stdout: str) -> list[str]:
    """Extract changed paths from ``git status --porcelain`` (v1) output.

    The porcelain-v1 short format is ``XY <path>`` where ``XY`` is a two-char
    status code followed by a single space (3 leading chars). The path begins at
    index 3 and must be sliced *without* stripping the line first: for unstaged
    entries the X column is a space (e.g. ``" M go.mod"``), and stripping it would
    shift the slice and drop the first character of the filename (``"o.mod"``).

    Paths containing spaces or non-ASCII characters are wrapped in double quotes
    by git with C-style escapes; ``_git_unquote`` decodes them before the path
    is returned so callers can use them directly as ``git add --`` arguments.

    Rename entries (``R  old -> new``) are returned as the *new* path only.
    """
    paths: list[str] = []
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1].strip()
        entry = _git_unquote(entry)
        if entry:
            paths.append(entry)
    return paths


def porcelain_changed_paths(repo: Path) -> list[str]:
    proc = run_git(repo, "status", "--porcelain", timeout=60)
    if proc.returncode != 0:
        return []
    return _parse_porcelain(proc.stdout)


def filter_safe_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    safe: list[str] = []
    blocked: list[str] = []
    for p in paths:
        if is_forbidden_commit_path(p):
            blocked.append(p)
        else:
            safe.append(p)
    return safe, blocked


def _bundle_started_dirty(bundle: RepoBundle) -> bool:
    return any(wr.started_dirty for wr in bundle.workspaces)


def _bundle_apply_ok(
    bundle: RepoBundle,
    *,
    commit_on_fail: bool,
) -> tuple[bool, str | None]:
    if not bundle.workspaces:
        return False, "no workspaces"
    for wr in bundle.workspaces:
        if wr.status == "skip":
            err = wr.error or ""
            if "dirty git worktree" in err:
                return False, "skipped dirty worktree"
            if wr.error == "fail-fast":
                return False, "fail-fast"
        if wr.status == "fail":
            if wr.verify_error and not commit_on_fail:
                return False, "verify failed"
            if not commit_on_fail:
                return False, wr.verify_error or wr.error or "apply failed"
    if bundle.any_fail() and not commit_on_fail:
        return False, "workspace failures"
    return True, None


def _has_actionable_repo_changes(bundle: RepoBundle) -> bool:
    for wr in bundle.workspaces:
        if wr.changes or wr.remediations:
            return True
    for ab in bundle.actions:
        if ab.status == "ok" and ab.pin != ab.latest:
            return True
    return bool(porcelain_changed_paths(bundle.git_repo))


def classify_commit_subject(files: list[str]) -> str:
    """One-line Conventional Commits subject from changed paths."""
    names = {Path(p).name.lower() for p in files}
    if any(n.endswith(".yml") or n.endswith(".yaml") for n in names) and any(
        ".github" in p.replace("\\", "/") for p in files
    ):
        return "chore(ci): bump GitHub Actions pins"
    if any(n in ("go.mod", "go.sum") for n in names):
        return "chore(deps): update Go modules"
    if any(n.endswith(".toml") and "cargo" in n for n in names):
        return "chore(deps): update Rust lockfiles"
    if any(
        n in ("pyproject.toml", "uv.lock", "poetry.lock", "requirements.txt")
        for n in names
    ):
        return "chore(deps): update Python dependencies"
    if any(
        n
        in (
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
        )
        for n in names
    ):
        return "chore(deps): update Node lockfiles and manifests"
    return "chore(deps): apply repo-ops dependency updates"


def build_conventional_message(bundle: RepoBundle, files: list[str]) -> str:
    subject = classify_commit_subject(files)
    body_lines = [f"Automated post-apply commit for {bundle.label}."]
    if bundle.workspaces:
        changed_ws = [wr for wr in bundle.workspaces if wr.changes]
        if changed_ws:
            body_lines.append("")
            body_lines.append("Workspaces:")
            for wr in changed_ws[:8]:
                ws = wr.workspace
                body_lines.append(f"- {ws.label} ({ws.ecosystem}/{ws.manager})")
            if len(changed_ws) > 8:
                body_lines.append(f"- … and {len(changed_ws) - 8} more")
    body = "\n".join(body_lines)
    return f"{subject}\n\n{body}"


def plan_repo_commit(
    bundle: RepoBundle,
    *,
    mode: CommitMode,
    allow_dirty: bool,
    commit_on_fail: bool,
) -> CommitPlan:
    repo = bundle.git_repo
    if _bundle_started_dirty(bundle) and not allow_dirty:
        return CommitPlan(
            repo, bundle.label, "", skipped_reason="dirty worktree at start"
        )
    ok, reason = _bundle_apply_ok(bundle, commit_on_fail=commit_on_fail)
    if not ok:
        return CommitPlan(repo, bundle.label, "", skipped_reason=reason)
    if not _has_actionable_repo_changes(bundle):
        return CommitPlan(repo, bundle.label, "", skipped_reason="no changes to commit")
    files = porcelain_changed_paths(repo)
    if not files:
        return CommitPlan(repo, bundle.label, "", skipped_reason="clean worktree")
    if allow_dirty and _bundle_started_dirty(bundle):
        scoped = upgrade_scoped_paths(bundle, files)
        if not scoped:
            return CommitPlan(
                repo,
                bundle.label,
                "",
                skipped_reason="no upgrade-scoped changes to commit (dirty worktree)",
            )
        files = scoped
    safe, blocked = filter_safe_paths(files)
    if blocked:
        return CommitPlan(
            repo,
            bundle.label,
            "",
            skipped_reason=f"forbidden paths: {', '.join(blocked[:3])}"
            + ("…" if len(blocked) > 3 else ""),
        )
    if not safe:
        return CommitPlan(
            repo, bundle.label, "", skipped_reason="no safe paths to stage"
        )
    if mode == "agent":
        try:
            from repo_ops_upgrade.commit_agent_providers import (
                draft_message_with_commit_agent,
            )

            message = draft_message_with_commit_agent(bundle, safe)
        except Exception as exc:
            return CommitPlan(repo, bundle.label, "", skipped_reason=str(exc))
    else:
        message = build_conventional_message(bundle, safe)
    return CommitPlan(repo, bundle.label, message, files=safe)


def execute_commit_plan(plan: CommitPlan, *, dry_run: bool) -> CommitOutcome:
    if plan.skipped_reason:
        return CommitOutcome(
            plan.git_repo,
            plan.label,
            "skipped",
            detail=plan.skipped_reason,
        )
    if dry_run:
        return CommitOutcome(
            plan.git_repo,
            plan.label,
            "dry_run",
            message=plan.message,
            files=plan.files,
        )
    for path in plan.files:
        add = run_git(plan.git_repo, "add", "--", path, timeout=120)
        if add.returncode != 0:
            err = (add.stderr or add.stdout or "git add failed").strip()
            return CommitOutcome(
                plan.git_repo,
                plan.label,
                "failed",
                detail=f"git add {path!r}: {err}",
            )
    commit = run_git(
        plan.git_repo,
        "commit",
        "-m",
        plan.message,
        timeout=120,
    )
    if commit.returncode != 0:
        err = (commit.stderr or commit.stdout or "git commit failed").strip()
        return CommitOutcome(
            plan.git_repo,
            plan.label,
            "failed",
            detail=err,
        )
    return CommitOutcome(
        plan.git_repo,
        plan.label,
        "committed",
        message=plan.message,
        files=plan.files,
    )


def run_post_apply_commits(
    bundles: dict[Path, RepoBundle],
    *,
    mode: CommitMode,
    dry_run: bool = False,
    allow_dirty: bool = False,
    commit_on_fail: bool = False,
    quiet: bool = False,
) -> tuple[list[CommitOutcome], int]:
    """Plan and optionally execute one commit per eligible git repo."""
    if mode == "none":
        return [], 0
    outcomes: list[CommitOutcome] = []
    exit_code = 0
    for bundle in sorted(bundles.values(), key=lambda b: b.label):
        plan = plan_repo_commit(
            bundle,
            mode=mode,
            allow_dirty=allow_dirty,
            commit_on_fail=commit_on_fail,
        )
        outcome = execute_commit_plan(plan, dry_run=dry_run)
        outcomes.append(outcome)
        if outcome.status == "failed":
            exit_code = 1
        if not quiet:
            _print_outcome(outcome)
    return outcomes, exit_code


def _print_outcome(outcome: CommitOutcome) -> None:
    prefix = outcome.label
    if outcome.status == "dry_run":
        print(f"[commit dry-run] {prefix}")
        if outcome.message:
            print(outcome.message)
        if outcome.files:
            print("  files:")
            for f in outcome.files:
                print(f"    {f}")
    elif outcome.status == "committed":
        print(f"[commit] {prefix}: committed ({len(outcome.files)} files)")
    elif outcome.status == "skipped":
        print(f"[commit skip] {prefix}: {outcome.detail}")
    elif outcome.status == "failed":
        print(f"[commit fail] {prefix}: {outcome.detail}", file=sys.stderr)
