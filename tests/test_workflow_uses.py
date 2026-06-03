"""YAML-based GitHub Actions uses: discovery and apply (issue #19)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_upgrade.actions import scan_actions
from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    parse_uses_ref,
    parse_workflow_uses,
    update_workflow_pins,
    workflow_roundtrip_text,
)

_CI_SNIPPET = """\
name: CI
jobs:
  ci:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv run pytest -q
"""

_RELEASE_SNIPPET = """\
name: Release
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Upload release assets
        uses: softprops/action-gh-release@v3.0.0
        with:
          files: dist/*
"""


def test_parse_uses_ref_shapes() -> None:
    assert parse_uses_ref("actions/checkout@v4") == ("actions/checkout", "v4")
    assert parse_uses_ref("softprops/action-gh-release@v3.0.0") == (
        "softprops/action-gh-release",
        "v3.0.0",
    )


def test_parse_workflow_uses_list_item_steps(tmp_path: Path) -> None:
    wf = tmp_path / "ci.yml"
    wf.write_text(_CI_SNIPPET, encoding="utf-8")
    pins = parse_workflow_uses(wf, workflow_rel=".github/workflows/ci.yml")
    actions = {(p.action, p.pin) for p in pins}
    assert ("actions/checkout", "v4") in actions
    assert ("astral-sh/setup-uv", "v5") in actions
    assert len(pins) >= 2


def test_parse_workflow_uses_block_and_list_uses(tmp_path: Path) -> None:
    wf = tmp_path / "release.yml"
    wf.write_text(_RELEASE_SNIPPET, encoding="utf-8")
    pins = parse_workflow_uses(wf)
    actions = {p.action for p in pins}
    assert "actions/checkout" in actions
    assert "astral-sh/setup-uv" in actions
    assert "softprops/action-gh-release" in actions


def test_update_workflow_pins_no_stacked_suffix(tmp_path: Path) -> None:
    wf = tmp_path / "wf.yml"
    wf.write_text(
        "jobs:\n"
        "  short:\n"
        "    steps:\n"
        "      - uses: example/action@v3\n"
        "  long:\n"
        "    steps:\n"
        "      - uses: example/action@v3.19.0\n",
        encoding="utf-8",
    )
    update_workflow_pins(
        wf,
        [PinReplacement("example/action", "v3", "v3.19.0", line=None)],
    )
    text = wf.read_text(encoding="utf-8")
    assert "v3.19.0.19.0" not in text
    assert text.count("v3.19.0") >= 1


def test_scan_actions_discovers_checkout_list_item(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(_CI_SNIPPET, encoding="utf-8")

    def _tag_info(action: str, **kwargs: object) -> dict | None:
        if action in ("actions/checkout", "astral-sh/setup-uv"):
            return {"bare_majors": {4, 5, 6}, "max_major": 6}
        return None

    with (
        patch(
            "repo_ops_upgrade.actions.action_tag_info",
            side_effect=_tag_info,
        ),
        patch("repo_ops_upgrade.actions.shutil.which", return_value=None),
    ):
        bumps, scanned = scan_actions(tmp_path, apply=False)
    assert scanned == 1
    action_names = {b.action for b in bumps}
    assert "actions/checkout" in action_names
    assert "astral-sh/setup-uv" in action_names


def test_scan_actions_apply_no_stacked_suffix(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        "jobs:\n"
        "  a:\n"
        "    steps:\n"
        "      - uses: example/action@v3\n"
        "  b:\n"
        "    steps:\n"
        "      - uses: example/action@v3.19.0\n",
        encoding="utf-8",
    )
    with (
        patch(
            "repo_ops_upgrade.actions.action_tag_info",
            return_value={"bare_majors": {3}, "max_major": 3},
        ),
        patch("repo_ops_upgrade.actions.shutil.which", return_value=None),
    ):
        scan_actions(tmp_path, apply=True)
    text = wf.read_text(encoding="utf-8")
    assert "v3.19.0.19.0" not in text


@pytest.fixture
def repo_ops_workflows() -> Path:
    root = Path(__file__).resolve().parents[1]
    wf_dir = root / ".github" / "workflows"
    if not (wf_dir / "ci.yml").is_file():
        pytest.skip("repo workflows missing")
    return wf_dir


def test_parse_real_ci_workflow(repo_ops_workflows: Path) -> None:
    pins = parse_workflow_uses(repo_ops_workflows / "ci.yml")
    checkout = [p for p in pins if p.action == "actions/checkout"]
    assert checkout, "expected actions/checkout in repo ci.yml"
    assert checkout[0].pin == "v6.0.2"


def test_workflow_roundtrip_preserves_ci_yaml(repo_ops_workflows: Path) -> None:
    ci = repo_ops_workflows / "ci.yml"
    original = ci.read_text(encoding="utf-8")
    roundtripped = workflow_roundtrip_text(ci)
    assert roundtripped == original


def test_workflow_roundtrip_preserves_on_trigger(repo_ops_workflows: Path) -> None:
    from repo_ops_upgrade.workflow_uses import _yaml_loader

    data = _yaml_loader().load(repo_ops_workflows / "ci.yml")
    assert "on" in data
    on = data["on"]
    assert "push" in on
    assert "pull_request" in on
    assert on["push"]["branches"] == ["main"]


def test_update_workflow_pins_noop_minimal_diff(tmp_path: Path) -> None:
    wf = tmp_path / "noop.yml"
    text = _CI_SNIPPET
    wf.write_text(text, encoding="utf-8")
    changed = update_workflow_pins(wf, [])
    assert not changed
    assert wf.read_text(encoding="utf-8") == text
