"""``@types/vscode`` is held at the ``engines.vscode`` floor vsce enforces."""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_js.holds import resolve_package_hold
from repo_ops_models import Workspace


def _ws(tmp_path: Path, pkg: dict[str, object]) -> Workspace:
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    return Workspace(path=tmp_path, ecosystem="node", manager="bun", git_repo=tmp_path, label="x")


def test_types_vscode_held_at_engine_major_minor(tmp_path: Path) -> None:
    ws = _ws(tmp_path, {"engines": {"vscode": "^1.140.0"}})
    rule = resolve_package_hold(ws, "@types/vscode")
    assert rule is not None
    assert rule.max_version == "1.140"


def test_no_hold_without_engine_or_for_other_packages(tmp_path: Path) -> None:
    ws = _ws(tmp_path, {"engines": {"node": ">=22"}})
    assert resolve_package_hold(ws, "@types/vscode") is None
    ws = _ws(tmp_path, {"engines": {"vscode": "^1.140.0"}})
    assert resolve_package_hold(ws, "@types/node") is None
