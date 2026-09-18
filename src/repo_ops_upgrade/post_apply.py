"""Post-apply change detection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from repo_ops_core.constants import TIMEOUT_APPLY
from repo_ops_core.process import run
from repo_ops_core.version_utils import normalize_version
from repo_ops_lockfiles.constants import (
    GO_LOCKFILES,
    NODE_LOCKFILES,
    PY_LOCKFILES,
    RUST_LOCKFILES,
)
from repo_ops_lockfiles.snapshots import (
    diff_workspace_snapshots,
    restore_workspace_snapshot,
    snapshot_workspace_files,
)
from repo_ops_models import Change, Workspace

logger = logging.getLogger(__name__)

FAILED_WITH_CHANGES = "FAILED_WITH_CHANGES"
FAILED_CLEAN = "FAILED_CLEAN"

# Install commands that rebuild a dependency tree from the lockfile alone, and
# the tree they rebuild. Verify installs the upgraded lockfile, so restoring the
# lockfile without reinstalling leaves the upgraded packages installed.
_REINSTALL: dict[str, tuple[list[str], str]] = {
    "bun": (["bun", "install", "--frozen-lockfile"], "node_modules"),
    "pnpm": (["pnpm", "install", "--frozen-lockfile"], "node_modules"),
    "npm": (["npm", "ci"], "node_modules"),
    "yarn": (["yarn", "install", "--immutable"], "node_modules"),
    "uv": (["uv", "sync", "--locked"], ".venv"),
    "hatch": (["uv", "sync", "--locked"], ".venv"),
    "pdm": (["pdm", "sync", "--clean"], ".venv"),
}

WorktreeState = dict[str, bytes | None]


def collect_changes_after_apply(ws: Workspace) -> list[Change]:
    """Lightweight post-apply change detection for remediation context.

    Only reports changes evidenced by the git diff of lockfiles/manifests.
    Never falls back to the ecosystem checker: checker output is "updates
    still available", not "changes applied", and reporting it here counted
    graph-only upgrades that go mod tidy had already dropped.
    """
    repo = ws.git_repo
    rel = ws.path
    try:
        rel = ws.path.relative_to(repo)
    except ValueError:
        rel = ws.path
    rel_s = str(rel)
    lock_names = (
        list(NODE_LOCKFILES) + list(PY_LOCKFILES) + list(GO_LOCKFILES) + list(RUST_LOCKFILES)
    )
    _DEP_SECTIONS = frozenset(
        ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
    )

    proc = run(
        ["git", "diff", "--name-only", "--", *lock_names],
        repo,
        timeout=60,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        changes: list[Change] = []
        for line in proc.stdout.splitlines():
            p = line.strip()
            if rel_s != "." and not p.startswith(rel_s):
                continue
            fname = Path(p).name
            if fname == "package.json":
                proc2 = run(
                    ["git", "diff", "--", p],
                    repo,
                    timeout=60,
                )
                if proc2.returncode == 0:
                    in_dep_section = False
                    brace_depth = 0
                    dep_section_depth = -1
                    for diff_line in proc2.stdout.splitlines():
                        # Detect section header on context or added lines
                        sec_m = re.match(r'^[ +]\s*"([^"]+)":\s*\{', diff_line)
                        if sec_m:
                            brace_depth += 1
                            if sec_m.group(1) in _DEP_SECTIONS:
                                in_dep_section = True
                                dep_section_depth = brace_depth
                            else:
                                in_dep_section = False
                                dep_section_depth = -1
                            continue
                        # Detect section close — only exit the dep section when
                        # returning to the depth at which it opened, so nested
                        # objects (e.g. peer-dependency ranges with sub-keys) do
                        # not prematurely reset in_dep_section.
                        if re.match(r"^[ +]\s*\}", diff_line):
                            if in_dep_section and brace_depth == dep_section_depth:
                                in_dep_section = False
                                dep_section_depth = -1
                            brace_depth = max(0, brace_depth - 1)
                            continue
                        if not in_dep_section:
                            continue
                        dep_m = re.match(r'^\+\s*"([^"]+)":\s*"([^"]+)"', diff_line)
                        if dep_m:
                            changes.append(
                                Change(
                                    dep_m.group(1),
                                    "?",
                                    normalize_version(dep_m.group(2)),
                                )
                            )
            elif fname in set(lock_names):
                changes.append(Change(fname, "changed", "updated"))
        if changes:
            return changes
    return []


def _workspace_git_pathspec(ws: Workspace) -> str:
    try:
        rel = ws.path.resolve().relative_to(ws.git_repo.resolve())
    except ValueError:
        return "."
    return "." if rel == Path(".") else str(rel)


def workspace_has_apply_artifacts(
    ws: Workspace,
    before_snap: dict[Path, bytes],
) -> bool:
    """True when apply left lockfile/manifest or git changes under the workspace."""
    after_snap = snapshot_workspace_files(ws.path, ws.manager)
    if diff_workspace_snapshots(before_snap, after_snap, ws.manager):
        return True
    pathspec = _workspace_git_pathspec(ws)
    proc = run(
        ["git", "status", "--porcelain", "--", pathspec],
        ws.git_repo,
        timeout=60,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def classify_verify_fail_disposition(
    ws: Workspace,
    before_snap: dict[Path, bytes],
    *,
    reverted_ok: bool = True,
) -> str:
    if not reverted_ok:
        return FAILED_WITH_CHANGES
    if workspace_has_apply_artifacts(ws, before_snap):
        return FAILED_WITH_CHANGES
    return FAILED_CLEAN


def _dirty_paths(repo: Path) -> dict[str, str] | None:
    """Repo-relative dirty path -> porcelain status code, or None if git failed.

    Untracked files are listed individually so a file apply created inside a new
    directory is seen. Submodules are ignored: their pointers move under their
    own commits, possibly in a parallel bundle, never under this repo's apply.
    """
    proc = run(
        [
            "git",
            "status",
            "--porcelain",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=all",
        ],
        repo,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    out: dict[str, str] = {}
    fields = proc.stdout.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1  # rename origin field
        out[entry[3:]] = entry[:2]
    return out


def capture_worktree_state(repo: Path) -> WorktreeState | None:
    """Bytes of every dirty file in *repo* (None for a deleted one).

    Everything else in the worktree equals HEAD, so this plus HEAD is the full
    pre-apply content of every tracked or untracked, non-ignored file.
    """
    dirty = _dirty_paths(repo)
    if dirty is None:
        return None
    state: WorktreeState = {}
    for rel in dirty:
        p = repo / rel
        if p.is_dir():
            continue  # nested repository; never ours to snapshot or restore
        state[rel] = p.read_bytes() if p.is_file() else None
    return state


def restore_worktree_state(repo: Path, before: WorktreeState | None) -> bool:
    """Return every non-ignored file in *repo* to *before* (see capture).

    A file dirty now but clean before is checked out from HEAD, or deleted when
    untracked; a file dirty before gets its captured bytes back, so an
    operator's own edit in a named checkout survives even when apply rewrote that file.
    Returns False when the state could not be fully restored.
    """
    if before is None:
        logger.warning("no pre-apply worktree state for %s; git revert skipped", repo)
        return False
    now = _dirty_paths(repo)
    if now is None:
        return False
    checkout: list[str] = []
    for rel in set(now) | set(before):
        p = repo / rel
        if rel in before:
            want = before[rel]
            if want is None:
                p.unlink(missing_ok=True)
            elif not p.is_file() or p.read_bytes() != want:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(want)
        elif p.is_dir():
            continue
        elif now[rel] == "??":
            p.unlink(missing_ok=True)
        else:
            checkout.append(rel)
    if not checkout:
        return True
    proc = run(["git", "checkout", "HEAD", "--", *checkout], repo, timeout=120)
    if proc.returncode != 0:
        logger.warning(
            "git checkout failed during revert for %s (rc=%d): %s",
            repo,
            proc.returncode,
            (proc.stderr or proc.stdout).strip(),
        )
        return False
    return True


def reinstall_from_lockfile(ws: Workspace) -> bool:
    """Rebuild *ws*'s installed dependency tree from its (restored) lockfile.

    Skipped when the tree does not exist: nothing installed means nothing stale.
    """
    spec = _REINSTALL.get(ws.manager)
    if spec is None or not (ws.path / spec[1]).is_dir():
        return True
    cmd = spec[0]
    if cmd[0] == "uv" and not (ws.path / "uv.lock").is_file():
        return True
    proc = run(cmd, ws.path, timeout=TIMEOUT_APPLY)
    if proc.returncode != 0:
        logger.warning(
            "%s failed during revert for %s (rc=%d): %s",
            " ".join(cmd),
            ws.path,
            proc.returncode,
            (proc.stderr or proc.stdout).strip(),
        )
        return False
    return True


def revert_workspace_after_verify_fail(
    ws: Workspace,
    before_snap: dict[Path, bytes],
    before_tree: WorktreeState | None,
) -> bool:
    """Return the repo to its state before *ws* was applied, then reinstall.

    *before_tree* is :func:`capture_worktree_state` taken right before apply.
    Workspaces in one repo are applied serially, so every change since then is
    this workspace's: manifests, lockfiles and whatever a remediation rewrote.
    *before_snap* additionally restores lockfiles the repo gitignores.

    Returns False when any step failed and the repo may be partially reverted.
    """
    restore_workspace_snapshot(ws.path, before_snap, ws.manager)
    tree_ok = restore_worktree_state(ws.git_repo, before_tree)
    return reinstall_from_lockfile(ws) and tree_ok
