"""Regression tests for GitHub Actions pin apply (issue #5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_upgrade.actions import replace_action_pin_in_text, scan_actions


def test_replace_action_pin_skips_prefix_of_longer_tag() -> None:
    text = "uses: 8398a7/action-slack@v3.19.0\n"
    assert (
        replace_action_pin_in_text(text, "8398a7/action-slack", "v3", "v3.19.0")
        == text
    )


def test_replace_action_pin_updates_exact_uses_line() -> None:
    text = "uses: 8398a7/action-slack@v3\n"
    out = replace_action_pin_in_text(text, "8398a7/action-slack", "v3", "v3.19.0")
    assert out == "uses: 8398a7/action-slack@v3.19.0\n"
    assert "v3.19.0.19.0" not in out


def test_replace_action_pin_same_file_mixed_pins() -> None:
    text = (
        "      - uses: 8398a7/action-slack@v3\n"
        "      - uses: 8398a7/action-slack@v3.19.0\n"
    )
    out = replace_action_pin_in_text(text, "8398a7/action-slack", "v3", "v3.19.0")
    assert "uses: 8398a7/action-slack@v3.19.0\n" in out
    assert "v3.19.0.19.0" not in out


def test_scan_actions_apply_no_stacked_suffix(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        "jobs:\n"
        "  short:\n"
        "    uses: example/action@v3\n"
        "  long:\n"
        "    uses: example/action@v3.19.0\n",
        encoding="utf-8",
    )
    with patch(
        "repo_ops_upgrade.actions.action_latest_tag",
        return_value="v3.19.0",
    ):
        scan_actions(tmp_path, apply=True)
    text = wf.read_text(encoding="utf-8")
    assert "v3.19.0.19.0" not in text
    assert text.count("v3.19.0") >= 1
