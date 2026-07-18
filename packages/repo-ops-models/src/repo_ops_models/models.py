"""Data classes for upgrade workspaces, results, and GitHub Actions bumps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Shared marker substring for the dirty-worktree skip error message. Producers
# that set WorkspaceResult.error for a dirty-skip (e.g. repo_ops_upgrade's
# orchestration/report modules) should import and use this constant rather
# than hardcoding the literal, so any_dirty_skip() below stays in sync with
# the exact wording a future change might make.
DIRTY_SKIP_MARKER = "dirty git worktree"


@dataclass
class Change:
    name: str
    from_ver: str
    to: str


@dataclass
class Workspace:
    path: Path
    ecosystem: str
    manager: str
    git_repo: Path
    label: str


@dataclass
class WorkspaceResult:
    workspace: Workspace
    status: str = "ok"
    changes: list[Change] = field(default_factory=list)
    behind_latest: list[Change] = field(default_factory=list)
    overrides_audit: list[dict[str, str]] = field(default_factory=list)
    remediations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    verify_error: str | None = None
    verify_fail_disposition: str | None = None
    started_dirty: bool = False
    reverted_ok: bool = True
    elapsed_s: float | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "path": str(self.workspace.path),
            "ecosystem": self.workspace.ecosystem,
            "manager": self.workspace.manager,
            "status": self.status,
            "changes": [
                {"name": c.name, "from": c.from_ver, "to": c.to} for c in self.changes
            ],
            "behind_latest": [
                {"name": c.name, "from": c.from_ver, "to": c.to}
                for c in self.behind_latest
            ],
            "overrides_audit": self.overrides_audit,
            "remediations": self.remediations,
        }
        if self.error is not None:
            out["error"] = self.error
        if self.verify_error is not None:
            out["verify_error"] = self.verify_error
        if self.verify_fail_disposition is not None:
            out["verify_fail_disposition"] = self.verify_fail_disposition
        if self.started_dirty:
            out["started_dirty"] = True
        if not self.reverted_ok:
            out["reverted_ok"] = False
        if self.elapsed_s is not None:
            out["elapsed_s"] = round(self.elapsed_s, 2)
        return out


@dataclass
class ActionBump:
    workflow: str
    action: str
    pin: str
    latest: str
    status: str = "ok"
    detail: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "workflow": self.workflow,
            "action": self.action,
            "pin": self.pin,
            "latest": self.latest,
            "status": self.status,
        }
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass
class RepoBundle:
    git_repo: Path
    label: str
    workspaces: list[WorkspaceResult] = field(default_factory=list)
    actions: list[ActionBump] = field(default_factory=list)
    actions_workflows_scanned: int = 0

    def any_fail(self) -> bool:
        return any(w.status == "fail" for w in self.workspaces)

    def any_dirty_skip(self) -> bool:
        return any(
            w.status == "skip" and w.error and DIRTY_SKIP_MARKER in w.error
            for w in self.workspaces
        )

    def actions_summary(self) -> dict[str, int]:
        bumps = 0
        failures = 0
        behind_latest = 0
        policy_violations = 0
        # Dedupe the bump count by the same (action, pin) key report.py uses to
        # dedupe displayed rows, so this counter equals the number of rows shown.
        seen: set[tuple[str, str]] = set()
        for ab in self.actions:
            if ab.status == "fail":
                failures += 1
                continue
            if ab.status == "policy":
                policy_violations += 1
                continue
            detail_lower = (ab.detail or "").lower()
            # Count "newer major available; not auto-applied" as behind_latest,
            # not as an actionable bump. Other report-only cases (SHA-pin, no
            # floating major) are informational and excluded from both counters.
            if "newer major" in detail_lower and "not auto-applied" in detail_lower:
                key = (ab.action, ab.pin)
                if key not in seen:
                    seen.add(key)
                    behind_latest += 1
                continue
            if "report only" in detail_lower:
                continue
            if ab.pin != ab.latest:
                key = (ab.action, ab.pin)
                if key in seen:
                    continue
                seen.add(key)
                bumps += 1
        return {
            "workflows_scanned": self.actions_workflows_scanned,
            "bumps": bumps,
            "failures": failures,
            "behind_latest": behind_latest,
            "policy_violations": policy_violations,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "git_repo": str(self.git_repo),
            "label": self.label,
            "workspaces": [w.to_json() for w in self.workspaces],
            "actions": [a.to_json() for a in self.actions],
            "actions_summary": self.actions_summary(),
        }
