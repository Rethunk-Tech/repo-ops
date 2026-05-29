"""Human-readable upgrade reports."""

from __future__ import annotations

from pathlib import Path

from repo_ops.upgrade.models import RepoBundle


def print_human_report(
    bundles: dict[Path, RepoBundle],
    *,
    dry_run: bool,
    only_outdated: bool,
    top_n: int,
    include_actions: bool,
) -> None:
    mode = "plan" if dry_run else "apply"
    print(f"repos-upgrade-{mode}")
    print()
    for repo in sorted(bundles.values(), key=lambda b: b.label):
        print(f"## {repo.label}")
        for wr in repo.workspaces:
            ws = wr.workspace
            n = len(wr.changes)
            if only_outdated and n == 0 and wr.status == "ok":
                continue
            status = wr.status.upper()
            rel = ws.path
            try:
                rel = ws.path.relative_to(ws.git_repo)
            except ValueError:
                pass
            print(f"  [{status}] {rel} ({ws.ecosystem}/{ws.manager}) — {n} upgrade(s)")
            if wr.error:
                print(f"    error: {wr.error}")
            if wr.verify_error:
                print(f"    verify: {wr.verify_error}")
            for c in wr.changes[:top_n]:
                print(f"    {c.name} {c.from_ver} → {c.to}")
            if len(wr.changes) > top_n:
                print(f"    ... and {len(wr.changes) - top_n} more")
            for oa in wr.overrides_audit:
                print(f"    {oa.get('detail', oa)}")
            for rem in wr.remediations:
                if rem.get("status") != "ok" or rem.get("detail"):
                    print(
                        f"    [{rem.get('id')}] {rem.get('detail') or rem.get('status')}"
                    )
        if include_actions and repo.actions:
            print("  Actions:")
            for ab in repo.actions:
                print(
                    f"    {ab.workflow}: {ab.action}@{ab.pin} → {ab.latest} [{ab.status}]"
                )
        print()
