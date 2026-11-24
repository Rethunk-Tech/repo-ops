"""A bun workspaces apply must commit every member manifest it bumped."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from repo_ops_upgrade.post_apply_commit import upgrade_scoped_paths


def test_bun_root_apply_commits_workspace_member_manifests(tmp_path: Path) -> None:
    """Root owned_edits still include member package.json files bun bumped.

    A bun workspaces install can rewrite apps/*/package.json as a side effect
    of the root lockfile bump. The snapshot records only the root paths; if
    those exact owned_edits skip the nested-member match, the member
    manifests stay dirty and block rgit land.
    """
    (tmp_path / "package.json").write_text(
        '{"name":"root","workspaces":["apps/*"]}\n',
        encoding="utf-8",
    )
    (tmp_path / "bun.lock").write_text("{}\n", encoding="utf-8")
    for name in ("daemon", "web"):
        pkg = tmp_path / "apps" / name
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(
            f'{{"name":"{name}","dependencies":{{"@trpc/server":"^11.31.3"}}}}\n',
            encoding="utf-8",
        )

    wr = MagicMock()
    wr.owned_edits = {
        "package.json": MagicMock(applied=b""),
        "bun.lock": MagicMock(applied=b""),
    }
    wr.changes = [MagicMock()]
    wr.remediations = []
    wr.verify_skipped = False
    wr.adopted_dirty = []
    wr.workspace.path = tmp_path
    wr.workspace.manager = "bun"

    bundle = MagicMock()
    bundle.git_repo = tmp_path
    bundle.workspaces = [wr]
    bundle.actions = []

    porcelain = [
        "package.json",
        "bun.lock",
        "apps/daemon/package.json",
        "apps/web/package.json",
    ]
    assert upgrade_scoped_paths(bundle, porcelain) == porcelain
