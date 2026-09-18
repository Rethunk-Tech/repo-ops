"""YAML-based GitHub Actions uses: discovery and apply."""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_upgrade.actions import scan_actions
from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    _yaml_loader,
    parse_uses_ref,
    parse_workflow_uses,
    update_workflow_pins,
)


def _workflow_roundtrip_text(path: Path) -> str:
    """Load and dump a workflow file — a no-op should reproduce the source
    byte-for-byte; drift here means ``_yaml_loader``'s formatting settings
    no longer match this repo's workflow YAML conventions."""
    yaml = _yaml_loader()
    data = yaml.load(path)
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


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
    # Policy dogfood: this repo's own workflows are SHA-pinned with a
    # # vN.M.P audit-hint comment, matching the
    # supply-chain-hardening posture this repo enforces on every caller —
    # assert the shape, not a frozen value.
    assert re.fullmatch(r"[0-9a-f]{40}", checkout[0].pin), (
        f"repo ci.yml checkout should be SHA-pinned, got {checkout[0].pin!r}"
    )
    assert checkout[0].comment and re.fullmatch(r"v\d+\.\d+\.\d+", checkout[0].comment), (
        f"repo ci.yml checkout should carry a # vN.M.P audit-hint comment, got {checkout[0].comment!r}"
    )


def test_workflow_roundtrip_preserves_ci_yaml(repo_ops_workflows: Path) -> None:
    ci = repo_ops_workflows / "ci.yml"
    original = ci.read_text(encoding="utf-8")
    roundtripped = _workflow_roundtrip_text(ci)
    assert roundtripped == original


def test_workflow_roundtrip_preserves_on_trigger(repo_ops_workflows: Path) -> None:
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


# ---------------------------------------------------------------------------
# byte-preserving pin edits (no YAML reformat)
# ---------------------------------------------------------------------------

_SIX_SPACE_WORKFLOW = """\
name: Release
on: push
jobs:
  release:
      runs-on: ubuntu-latest
      steps:
            - name: Checkout
              uses: actions/checkout@v2  # keep this comment
            - name: Upload
              uses: softprops/action-gh-release@v2.0.0
            - run: echo hello  # unrelated comment
"""


def test_pin_bump_produces_exactly_one_line_diff(tmp_path: Path) -> None:
    """Bumping one pin must produce a single-line diff.

    A workflow with 6-space-indented steps and inline comments must have
    exactly one line changed (and only the @ref portion of that line) after
    a pin bump.  The old ruamel.yaml dump path would reindent the entire
    block-sequence, producing 51 insertions/51 deletions for a single bump.
    """
    wf = tmp_path / "release.yml"
    wf.write_text(_SIX_SPACE_WORKFLOW, encoding="utf-8")

    changed = update_workflow_pins(
        wf,
        [PinReplacement("actions/checkout", "v2", "v3", line=8)],
    )

    assert changed
    after = wf.read_text(encoding="utf-8")
    before_lines = _SIX_SPACE_WORKFLOW.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)

    # File length must not change.
    assert len(after_lines) == len(before_lines), (
        f"line count changed: {len(before_lines)} -> {len(after_lines)}"
    )

    # Exactly one line must differ.
    diffs = [
        (i, b, a)
        for i, (b, a) in enumerate(zip(before_lines, after_lines, strict=True), 1)
        if b != a
    ]
    assert len(diffs) == 1, f"expected 1 changed line, got {len(diffs)}: {diffs}"

    _changed_line_num, before_line, after_line = diffs[0]
    # The changed line must contain the new pin.
    assert "actions/checkout@v3" in after_line, after_line
    # The comment must be preserved.
    assert "# keep this comment" in after_line, after_line
    # Indentation must be unchanged.
    assert before_line.index("uses:") == after_line.index("uses:"), "indentation shifted"
