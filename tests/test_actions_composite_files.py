"""Composite-action `action.yml`/`action.yaml` files are scanned like workflows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import discover_action_files, scan_actions

_V10_2_0 = "cccccccccccccccccccccccccccccccccccccccc"
_V10_1_0 = "dddddddddddddddddddddddddddddddddddddddd"


def _make_tags_proc(*lines: str) -> MagicMock:
    return MagicMock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")


_COMPOSITE = """\
name: setup-python
description: Set up Python and uv
runs:
  using: composite
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@{sha}  # v10.1.0
      shell: bash
"""


def _write_action(path: Path, sha: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_COMPOSITE.format(sha=sha), encoding="utf-8")
    return path


def test_discover_action_files_finds_nested_action_yml(tmp_path: Path) -> None:
    _write_action(tmp_path / "setup-python" / "action.yml", _V10_1_0)
    (tmp_path / "unrelated.txt").write_text("noop", encoding="utf-8")
    found = discover_action_files(tmp_path)
    assert found == [tmp_path / "setup-python" / "action.yml"]


def test_composite_action_yml_pin_bumps(tmp_path: Path) -> None:
    """A pinned third-party `uses:` inside a composite action.yml advances like a workflow pin."""
    action = _write_action(tmp_path / "setup-python" / "action.yml", _V10_1_0)
    tags_proc = _make_tags_proc(
        f"v10.2.0 {_V10_2_0}",
        f"v10.1.0 {_V10_1_0}",
        f"v10 {_V10_2_0}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(tmp_path, apply=True)

    assert scanned == 1
    setup_uv = [b for b in bumps if b.action == "astral-sh/setup-uv"]
    assert len(setup_uv) == 1, bumps
    assert setup_uv[0].workflow == "setup-python/action.yml"
    assert setup_uv[0].latest == _V10_2_0

    text = action.read_text(encoding="utf-8")
    assert f"@{_V10_2_0}" in text
    assert "# v10.2.0" in text


def test_composite_action_under_excluded_glob_is_not_scanned(tmp_path: Path) -> None:
    """A pin under an excluded workspace glob is neither scanned nor rewritten."""
    action = _write_action(
        tmp_path / ".github" / "test-fixtures" / "broken" / "action.yml", _V10_1_0
    )
    original = action.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(
        f"v10.2.0 {_V10_2_0}",
        f"v10.1.0 {_V10_1_0}",
        f"v10 {_V10_2_0}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(
            tmp_path, apply=True, exclude_patterns=[".github/test-fixtures/*"]
        )

    assert scanned == 0
    assert bumps == []
    assert action.read_text(encoding="utf-8") == original
