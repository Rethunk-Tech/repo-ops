"""Regression tests for GitHub Actions pin apply (issues #5, #19)."""

from __future__ import annotations

from pathlib import Path

from repo_ops_upgrade.workflow_uses import PinReplacement, parse_uses_ref, update_workflow_pins


def test_parse_uses_ref_skips_prefix_of_longer_tag() -> None:
    assert parse_uses_ref("8398a7/action-slack@v3.19.0") == (
        "8398a7/action-slack",
        "v3.19.0",
    )


def test_update_pin_skips_non_matching_longer_tag(tmp_path: Path) -> None:
    wf = tmp_path / "wf.yml"
    wf.write_text("uses: 8398a7/action-slack@v3.19.0\n", encoding="utf-8")
    update_workflow_pins(
        wf,
        [PinReplacement("8398a7/action-slack", "v3", "v3.19.0")],
    )
    assert wf.read_text(encoding="utf-8") == "uses: 8398a7/action-slack@v3.19.0\n"


def test_update_pin_exact_uses_line(tmp_path: Path) -> None:
    wf = tmp_path / "wf.yml"
    wf.write_text("uses: 8398a7/action-slack@v3\n", encoding="utf-8")
    update_workflow_pins(
        wf,
        [PinReplacement("8398a7/action-slack", "v3", "v3.19.0")],
    )
    out = wf.read_text(encoding="utf-8")
    assert out == "uses: 8398a7/action-slack@v3.19.0\n"
    assert "v3.19.0.19.0" not in out


def test_update_pin_same_file_mixed_pins(tmp_path: Path) -> None:
    wf = tmp_path / "wf.yml"
    wf.write_text(
        "jobs:\n"
        "  x:\n"
        "    steps:\n"
        "      - uses: 8398a7/action-slack@v3\n"
        "      - uses: 8398a7/action-slack@v3.19.0\n",
        encoding="utf-8",
    )
    update_workflow_pins(
        wf,
        [PinReplacement("8398a7/action-slack", "v3", "v3.19.0")],
    )
    out = wf.read_text(encoding="utf-8")
    assert "uses: 8398a7/action-slack@v3.19.0" in out
    assert "v3.19.0.19.0" not in out
