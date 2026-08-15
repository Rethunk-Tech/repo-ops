"""Optional post-apply git commits (conventional or pluggable commit-agent)."""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from repo_ops_core.git import run_git
from repo_ops_js.workspaces import path_is_yarn_workspace_child, yarn_workspace_members
from repo_ops_lockfiles.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops_models import RepoBundle
from repo_ops_upgrade.submodules import SubmoduleLink

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
    if manager in ("uv", "hatch", "pip-tools", "poetry", "pdm", "pipenv"):
        return frozenset(PY_LOCKFILES)
    if manager == "go":
        return frozenset(GO_LOCKFILES)
    if manager == "rust":
        return frozenset(RUST_LOCKFILES)
    return frozenset()


def _manifest_names_for_manager(manager: str) -> frozenset[str]:
    """Manifest basenames for a manager (the file a bump rewrites versions in).

    Used only for root workspaces (``rel == "."``), where there is no directory
    prefix to scope by, so the manifest must be matched by name to avoid a partial
    commit (lockfile staged, manifest dropped). A subdirectory workspace's prefix
    already captures its manifest. Issue #108.
    """
    if manager in ("yarn", "npm", "pnpm", "bun"):
        names = {"package.json"}
        if manager == "pnpm":
            names.add("pnpm-workspace.yaml")
        return frozenset(names)
    if manager in ("uv", "hatch", "pip-tools", "poetry", "pdm"):
        return frozenset({"pyproject.toml"})
    if manager == "pipenv":
        return frozenset({"Pipfile"})
    if manager == "go":
        return frozenset({"go.mod"})
    if manager == "rust":
        return frozenset({"Cargo.toml"})
    return frozenset()


def upgrade_scoped_paths(
    bundle: RepoBundle,
    porcelain: list[str],
    *,
    extra_gitlink_paths: set[str] | None = None,
) -> list[str]:
    """Porcelain paths that belong to this upgrade (not unrelated dirty files).

    Only paths this run actually modified are included:
    - Root-workspace lockfiles/manifests matched by basename (no prefix ambiguity).
    - Subdirectory-workspace files matched by directory prefix with a "/" boundary
      so "packages/foo" never matches "packages/foobar". Issue #116 / #prefix-boundary.
    - Workflow files explicitly bumped by an actions run (exact path match).
    - When the bundle has actions bumps but no workspaces, only the bumped workflow
      file(s) are staged — pre-existing dirty files are never swept in. Issue #116.
    - Submodule gitlink paths explicitly advanced by apply_submodule_tag_advances
      (passed via ``extra_gitlink_paths``) so pointer bumps are committed alongside
      dependency updates rather than left staged-but-uncommitted. Task #18.
    - Nested package.json files belonging to a declared yarn/npm/pnpm/bun
      workspace member, when the *scanned* workspace is the monorepo root
      (``rel == "."``) — a root-scoped install/upgrade can resolve a
      peer/group-constrained bump into a member's manifest as a side effect,
      and that manifest carries a directory component the basename-only
      root match excludes. Scoped to the root workspace's own declared
      ``workspaces`` globs so unrelated dirty manifests are never swept in.
      Issue #153.
    - Files an applied remediation hook declares it touched (its own ``files``
      callback, e.g. ``biome_schema`` rewriting ``biome.json``'s schema URL),
      matched by exact repo-relative path. A remediation writes files outside
      the manager's own manifest/lockfile, so neither the root-basename match
      nor the subdirectory-prefix match sees them when the *scanned* workspace
      is the monorepo root. Sourced from the hook's own declaration rather than
      hand-enumerated per manager, so it also covers future remediations that
      write a root-level file. Issue #154.
    """
    # root_wanted: basenames valid at the repo root (no subdirectory prefix).
    root_wanted: set[str] = set()
    # sub_prefixes: dir prefixes for subdirectory workspaces (with "/" boundary).
    sub_prefixes: set[str] = set()
    # action_paths: exact relative paths of bumped workflow files.
    action_paths: set[str] = set()
    # node_workspace_roots: (root path, member glob patterns) for root-scanned
    # node workspaces monorepos, used to match nested member package.json files.
    node_workspace_roots: list[tuple[Path, set[str]]] = []
    # remediation_paths: exact repo-relative paths an applied remediation hook
    # declared it wrote via its own `files` callback.
    remediation_paths: set[str] = set()

    for wr in bundle.workspaces:
        if not (wr.changes or wr.remediations):
            continue
        try:
            rel = wr.workspace.path.resolve().relative_to(bundle.git_repo.resolve())
        except ValueError:
            rel = Path(".")
        if rel == Path("."):
            # Root workspace: scope by basename only; also include the manifest
            # so a root bump never commits the lockfile while dropping the manifest
            # (partial commit). Issue #108.
            root_wanted.update(_lockfile_names_for_manager(wr.workspace.manager))
            root_wanted.update(_manifest_names_for_manager(wr.workspace.manager))
            if wr.workspace.manager in ("yarn", "npm", "pnpm", "bun"):
                members = yarn_workspace_members(wr.workspace.path)
                if members:
                    node_workspace_roots.append((wr.workspace.path.resolve(), members))
        else:
            # Subdirectory workspace: scope by prefix with a strict "/" boundary
            # so "packages/foo" cannot match "packages/foobar". Issue #116.
            sub_prefixes.add(str(rel).replace("\\", "/"))
        for entry in wr.remediations:
            if entry.get("status") != "applied":
                continue
            for f in entry.get("files") or []:
                try:
                    rem_rel = (
                        (wr.workspace.path / f)
                        .resolve()
                        .relative_to(bundle.git_repo.resolve())
                    )
                except ValueError:
                    continue
                remediation_paths.add(str(rem_rel).replace("\\", "/"))

    for ab in bundle.actions:
        if ab.workflow and ab.status == "ok" and ab.pin != ab.latest:
            # Stage only workflow files whose pins were actually changed this run.
            # Report-only, policy-fail, or already-current bumps (pin == latest)
            # must not cause a pre-dirty workflow to be staged.
            action_paths.add(ab.workflow.replace("\\", "/"))

    scoped: list[str] = []
    for path in porcelain:
        norm = path.replace("\\", "/")
        base = Path(norm).name
        # Root-workspace lockfile/manifest: no directory component expected.
        if base in root_wanted and "/" not in norm:
            scoped.append(path)
            continue
        # Exact bumped workflow file.
        if norm in action_paths:
            scoped.append(path)
            continue
        # Submodule gitlink paths advanced by apply_submodule_tag_advances.
        if extra_gitlink_paths and norm in extra_gitlink_paths:
            scoped.append(path)
            continue
        # File an applied remediation hook declared it touched. Issue #154.
        if norm in remediation_paths:
            scoped.append(path)
            continue
        # Subdirectory workspace: prefix must match with a "/" boundary.
        matched = False
        for prefix in sub_prefixes:
            if norm == prefix or norm.startswith(f"{prefix}/"):
                scoped.append(path)
                matched = True
                break
        if matched:
            continue
        # Nested workspace-member package.json touched by a root-scanned node
        # workspaces monorepo apply (e.g. yarn resolving a peer/group-constrained
        # bump into a member package). Issue #153.
        if base == "package.json" and node_workspace_roots:
            member_dir = (bundle.git_repo / norm).resolve().parent
            for root_path, members in node_workspace_roots:
                if path_is_yarn_workspace_child(member_dir, root_path, members):
                    scoped.append(path)
                    break
    return scoped


def _parse_porcelain(stdout: str) -> list[str]:
    """Extract changed paths from ``git status --porcelain -z`` output.

    With ``-z`` each record is NUL-terminated and paths are emitted verbatim
    (no quoting/escaping), so no unquoting step is needed. A rename/copy
    record (status code ``R`` or ``C`` in either column) is followed by a
    second, separate NUL-terminated field carrying the origin path — that
    field is consumed but discarded so only the new path is returned.
    """
    fields = stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    paths: list[str] = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        code = entry[:2]
        path = entry[3:]
        if "R" in code or "C" in code:
            i += 1  # origin path field; consumed but discarded
        if path:
            paths.append(path)
    return paths


def porcelain_changed_paths(repo: Path) -> list[str]:
    proc = run_git(repo, "status", "--porcelain", "-z", timeout=60)
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
        # Actions-only repo (workflows but no package manifest): still committable
        # when an action bump was actually applied. The porcelain check downstream
        # is the real arbiter, so a report-only bump (pin == latest) still skips as
        # "no changes to commit". Issue #107.
        if any(ab.status == "ok" and ab.pin != ab.latest for ab in bundle.actions):
            return True, None
        return False, "no workspaces"
    for wr in bundle.workspaces:
        # A workspace skipped as dirty must NOT veto the whole repo — a clean
        # sibling's bump still commits. The dirty workspace contributes no
        # wr.changes, so upgrade_scoped_paths excludes it and its dirty files are
        # never staged; if every workspace was skipped, scoping yields nothing and
        # the commit skips as "no upgrade-scoped changes". Issue #108. (fail-fast,
        # which signals an aborted run, still vetoes.)
        if wr.status == "skip" and wr.error == "fail-fast":
            return False, "fail-fast"
        if wr.status == "fail":
            if wr.verify_error and not commit_on_fail:
                return False, f"verify failed: {wr.verify_error}"
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
    """One-line Conventional Commits subject from changed paths.

    Enumerates ALL matched ecosystems so mixed commits are not misattributed to
    whichever ecosystem happened to be checked first (e.g. a yarn.lock + workflow
    commit must not become "chore(ci): bump GitHub Actions pins"). Issue #116.
    Single-ecosystem commits keep the same subjects as before so existing callers
    are unaffected. Mixed commits produce a compound subject with scope "deps,ci"
    or "deps" as appropriate.
    """
    names = {Path(p).name.lower() for p in files}
    has_ci = any(n.endswith(".yml") or n.endswith(".yaml") for n in names) and any(
        ".github" in p.replace("\\", "/") for p in files
    )
    has_go = any(n in ("go.mod", "go.sum") for n in names)
    has_rust = any(
        n == "cargo.lock" or (n.endswith(".toml") and "cargo" in n) for n in names
    )
    has_python = any(
        n
        in (
            "pyproject.toml",
            "uv.lock",
            "poetry.lock",
            "pdm.lock",
            "pipfile.lock",
            "requirements.txt",
            "requirements.in",
        )
        for n in names
    )
    has_deno = any(n in ("deno.lock", "deno.json", "deno.jsonc") for n in names)
    has_node = any(
        n
        in (
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "yarn.lock",
            "bun.lock",
            "bun.lockb",
        )
        for n in names
    )

    dep_parts: list[str] = []
    if has_go:
        dep_parts.append("Go modules")
    if has_rust:
        dep_parts.append("Rust lockfiles")
    if has_python:
        dep_parts.append("Python dependencies")
    if has_node:
        dep_parts.append("Node lockfiles and manifests")
    if has_deno:
        dep_parts.append("Deno lockfiles")

    has_deps = bool(dep_parts)

    if not has_deps and not has_ci:
        return "chore(deps): apply repo-ops dependency updates"
    if has_ci and not has_deps:
        # Actions-only: keep the original single-ecosystem subject.
        return "chore(ci): bump GitHub Actions pins"
    if has_deps and not has_ci:
        if len(dep_parts) == 1:
            # Single ecosystem: keep the original subject wording.
            labels = {
                "Go modules": "chore(deps): update Go modules",
                "Rust lockfiles": "chore(deps): update Rust lockfiles",
                "Python dependencies": "chore(deps): update Python dependencies",
                "Node lockfiles and manifests": (
                    "chore(deps): update Node lockfiles and manifests"
                ),
                "Deno lockfiles": "chore(deps): update Deno lockfiles",
            }
            return labels[dep_parts[0]]
        # Multiple dep ecosystems, no CI: "deps" scope, enumerate them.
        parts_str = ", ".join(dep_parts[:-1]) + ", and " + dep_parts[-1]
        return f"chore(deps): update {parts_str}"
    # Mixed deps + CI pins.
    if len(dep_parts) == 1:
        parts_str = dep_parts[0]
    else:
        parts_str = ", ".join(dep_parts[:-1]) + ", and " + dep_parts[-1]
    return f"chore(deps,ci): update {parts_str} and CI pins"


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
    extra_gitlink_paths: set[str] | None = None,
) -> CommitPlan:
    repo = bundle.git_repo
    ok, reason = _bundle_apply_ok(bundle, commit_on_fail=commit_on_fail)
    if not ok:
        return CommitPlan(repo, bundle.label, "", skipped_reason=reason)
    if not _has_actionable_repo_changes(bundle):
        return CommitPlan(repo, bundle.label, "", skipped_reason="no changes to commit")
    files = porcelain_changed_paths(repo)
    if not files:
        return CommitPlan(repo, bundle.label, "", skipped_reason="clean worktree")
    # Always scope to paths this run actually modified so that pre-existing dirty
    # files (e.g. an operator-kept yarn.lock) are never swept into a commit, even
    # when the repo was clean at run-start. Issue #116 generalises Issue #108.
    scoped = upgrade_scoped_paths(
        bundle, files, extra_gitlink_paths=extra_gitlink_paths
    )
    if not scoped:
        skip_reason = (
            "no upgrade-scoped changes to commit (dirty worktree)"
            if _bundle_started_dirty(bundle)
            else "no upgrade-scoped changes to commit"
        )
        return CommitPlan(repo, bundle.label, "", skipped_reason=skip_reason)
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
    max_workers: int = 8,
    submodule_gitlink_map: dict[Path, set[str]] | None = None,
) -> tuple[list[CommitOutcome], int]:
    """Plan and optionally execute one commit per eligible git repo.

    Bundles are processed in parallel (each is an independent git repo so commits
    do not race). Output ordering is deterministic: outcomes are sorted by label
    before printing, matching the serial behaviour. max_workers caps the thread pool.

    ``submodule_gitlink_map`` maps a parent git_repo path to the set of submodule
    relative paths whose gitlinks were staged by apply_submodule_tag_advances.
    These paths are passed to upgrade_scoped_paths so pointer bumps are committed
    alongside dependency updates rather than left staged-but-uncommitted. Task #18.
    """
    if mode == "none":
        return [], 0

    sorted_bundles = sorted(bundles.values(), key=lambda b: b.label)

    def _process(bundle: RepoBundle) -> CommitOutcome:
        gitlinks = (submodule_gitlink_map or {}).get(bundle.git_repo)
        plan = plan_repo_commit(
            bundle,
            mode=mode,
            allow_dirty=allow_dirty,
            commit_on_fail=commit_on_fail,
            extra_gitlink_paths=gitlinks,
        )
        return execute_commit_plan(plan, dry_run=dry_run)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit in label order so futures list is already sorted.
        futures = [executor.submit(_process, b) for b in sorted_bundles]
        outcomes = [f.result() for f in futures]

    exit_code = 0
    for outcome in outcomes:
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


# ---------------------------------------------------------------------------
# Umbrella submodule pointer-bump commits (opt-in via REPO_OPS_COMMIT_UMBRELLA=1)
# ---------------------------------------------------------------------------

_COMMIT_UMBRELLA_ENV = "REPO_OPS_COMMIT_UMBRELLA"


def _dirty_gitlink_paths_for_children(
    umbrella: Path,
    submodule_paths: list[str],
    committed_child_repos: set[Path],
    submodule_links: list[SubmoduleLink],
) -> list[str]:
    """Return submodule gitlink paths in *umbrella* that: (a) are dirty and
    (b) correspond to a child repo that received a commit this run.

    Only paths present in *submodule_paths* (derived from SubmoduleLinks whose
    parent == umbrella) that also appear in *committed_child_repos* are
    eligible — unrelated dirty files are never touched.
    """
    # Map submodule path → child git_repo so we can cross-check against
    # committed_child_repos.
    path_to_child: dict[str, Path] = {}
    for link in submodule_links:
        if link.parent.resolve() == umbrella.resolve():
            path_to_child[link.path.replace("\\", "/")] = link.git_repo.resolve()

    # Subset: only submodule paths whose children were committed this run.
    eligible: set[str] = {
        p for p, child in path_to_child.items() if child in committed_child_repos
    }
    if not eligible:
        return []

    proc = run_git(umbrella, "status", "--porcelain", "-z", timeout=60)
    if proc.returncode != 0:
        return []

    dirty = {
        norm
        for path in _parse_porcelain(proc.stdout)
        if (norm := path.replace("\\", "/")) in eligible
    }
    return sorted(dirty)


def commit_umbrella_submodule_pointers(
    submodule_links: list[SubmoduleLink],
    child_outcomes: list[CommitOutcome],
    *,
    mode: CommitMode,
    dry_run: bool = False,
    quiet: bool = False,
) -> tuple[list[CommitOutcome], int]:
    """Commit submodule pointer bumps in umbrella repos after child commits.

    Enabled only when ``REPO_OPS_COMMIT_UMBRELLA=1`` is set in the environment
    and *mode* is ``"conventional"`` or ``"agent"`` (same gate as the child
    commit phase).  One commit per eligible umbrella repo is produced; each
    stages only the gitlink paths corresponding to children that received a
    commit this run — pre-existing dirty paths are never touched.

    Returns ``(outcomes, exit_code)`` matching ``run_post_apply_commits``.
    """
    if mode == "none":
        return [], 0
    if not os.environ.get(_COMMIT_UMBRELLA_ENV, "").strip():
        return [], 0
    if not submodule_links:
        return [], 0

    # Set of child git repos that got a real commit (not dry_run, skip, fail).
    committed_children: set[Path] = {
        o.git_repo.resolve()
        for o in child_outcomes
        if o.status in ("committed", "dry_run")
    }
    if not committed_children:
        return [], 0

    # Collect unique umbrella repos.
    umbrella_repos: dict[Path, list[str]] = {}
    for link in submodule_links:
        parent = link.parent.resolve()
        umbrella_repos.setdefault(parent, []).append(link.path.replace("\\", "/"))

    outcomes: list[CommitOutcome] = []
    exit_code = 0

    for umbrella, submodule_paths in sorted(
        umbrella_repos.items(), key=lambda x: str(x[0])
    ):
        label = umbrella.name

        dirty_paths = _dirty_gitlink_paths_for_children(
            umbrella,
            submodule_paths,
            committed_children,
            submodule_links,
        )
        if not dirty_paths:
            # No gitlinks to bump — skip silently (no output to keep noise down).
            continue

        n = len(dirty_paths)
        child_names = ", ".join(Path(p).name for p in sorted(dirty_paths))
        subject = f"chore(submodules): bump {n} child pin(s)"
        body = (
            f"Automated pointer-bump commit for {label}.\n\n"
            f"Children updated: {child_names}"
        )
        message = f"{subject}\n\n{body}"

        if dry_run:
            outcome = CommitOutcome(
                umbrella,
                label,
                "dry_run",
                message=message,
                files=dirty_paths,
            )
            outcomes.append(outcome)
            if not quiet:
                _print_outcome(outcome)
            continue

        # Stage exactly the qualifying gitlink paths.
        add_failed = False
        for p in dirty_paths:
            add = run_git(umbrella, "add", "--", p, timeout=120)
            if add.returncode != 0:
                err = (add.stderr or add.stdout or "git add failed").strip()
                outcome = CommitOutcome(
                    umbrella,
                    label,
                    "failed",
                    detail=f"git add {p!r}: {err}",
                )
                outcomes.append(outcome)
                if not quiet:
                    _print_outcome(outcome)
                exit_code = 1
                add_failed = True
                break
        if add_failed:
            continue

        commit = run_git(umbrella, "commit", "-m", message, timeout=120)
        if commit.returncode != 0:
            err = (commit.stderr or commit.stdout or "git commit failed").strip()
            outcome = CommitOutcome(umbrella, label, "failed", detail=err)
            outcomes.append(outcome)
            if not quiet:
                _print_outcome(outcome)
            exit_code = 1
            continue

        outcome = CommitOutcome(
            umbrella,
            label,
            "committed",
            message=message,
            files=dirty_paths,
        )
        outcomes.append(outcome)
        if not quiet:
            _print_outcome(outcome)

    return outcomes, exit_code
