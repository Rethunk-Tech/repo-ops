"""Data classes for upgrade workspaces, results, and GitHub Actions bumps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
            "error": self.error,
            "verify_error": self.verify_error,
        }
        if self.verify_fail_disposition is not None:
            out["verify_fail_disposition"] = self.verify_fail_disposition
        if not self.reverted_ok:
            out["reverted_ok"] = False
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
        return {
            "workflow": self.workflow,
            "action": self.action,
            "pin": self.pin,
            "latest": self.latest,
            "status": self.status,
            "detail": self.detail,
        }


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
            w.status == "skip" and w.error and "dirty git worktree" in w.error
            for w in self.workspaces
        )

    def actions_summary(self) -> dict[str, int]:
        bumps = 0
        failures = 0
        behind_latest = 0
        for ab in self.actions:
            if ab.status == "fail":
                failures += 1
                continue
            detail_lower = (ab.detail or "").lower()
            # Count "newer major available; not auto-applied" as behind_latest,
            # not as an actionable bump. Other report-only cases (SHA-pin, no
            # floating major) are informational and excluded from both counters.
            if "newer major" in detail_lower and "not auto-applied" in detail_lower:
                behind_latest += 1
                continue
            if "report only" in detail_lower:
                continue
            if ab.pin != ab.latest:
                bumps += 1
        return {
            "workflows_scanned": self.actions_workflows_scanned,
            "bumps": bumps,
            "failures": failures,
            "behind_latest": behind_latest,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "git_repo": str(self.git_repo),
            "label": self.label,
            "workspaces": [w.to_json() for w in self.workspaces],
            "actions": [a.to_json() for a in self.actions],
            "actions_summary": self.actions_summary(),
        }
