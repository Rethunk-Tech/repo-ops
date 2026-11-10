"""Regression tests for GitHub Actions pin apply."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_upgrade.workflow_uses import (
    PinReplacement,
    WorkflowParseError,
    parse_uses_ref,
    update_workflow_pins,
)


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
        [PinReplacement("8398a7/action-slack", "v3", "v3.19.0", line=1)],
    )
    assert wf.read_text(encoding="utf-8") == "uses: 8398a7/action-slack@v3.19.0\n"


def test_update_pin_exact_uses_line(tmp_path: Path) -> None:
    wf = tmp_path / "wf.yml"
    wf.write_text("uses: 8398a7/action-slack@v3\n", encoding="utf-8")
    update_workflow_pins(
        wf,
        [PinReplacement("8398a7/action-slack", "v3", "v3.19.0", line=1)],
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
        [PinReplacement("8398a7/action-slack", "v3", "v3.19.0", line=4)],
    )
    out = wf.read_text(encoding="utf-8")
    assert "uses: 8398a7/action-slack@v3.19.0" in out
    assert "v3.19.0.19.0" not in out


def test_update_pin_keeps_crlf_line_endings(tmp_path: Path) -> None:
    """read_text's newline translation would turn a CRLF file into an LF whole-file diff."""
    wf = tmp_path / "wf.yml"
    body = (
        b"name: ci\r\njobs:\r\n  x:\r\n    steps:\r\n"
        b"      - uses: actions/checkout@v3\r\n# keep\r\n"
    )
    wf.write_bytes(body)

    assert update_workflow_pins(wf, [PinReplacement("actions/checkout", "v3", "v4", line=5)])

    out = wf.read_bytes()
    assert out == body.replace(b"checkout@v3", b"checkout@v4")
    assert out.count(b"\r\n") == body.count(b"\r\n")


def test_update_pin_preserves_non_utf8_bytes(tmp_path: Path) -> None:
    """A workflow with invalid UTF-8 must not raise; only the pin line changes."""
    wf = tmp_path / "wf.yml"
    body = b"uses: actions/checkout@v3\n# caf\xe9\n"
    wf.write_bytes(body)

    assert update_workflow_pins(wf, [PinReplacement("actions/checkout", "v3", "v4", line=1)])

    assert wf.read_bytes() == b"uses: actions/checkout@v4\n# caf\xe9\n"


def test_update_workflow_pins_write_oserror_raises_parse_error(
    tmp_path: Path,
) -> None:
    """OSError on write must be caught and re-raised as WorkflowParseError."""
    wf = tmp_path / "wf.yml"
    wf.write_text("uses: actions/checkout@v3\n", encoding="utf-8")
    # ruamel.yaml.load uses Path.open internally; patch only write mode so load succeeds.
    _real_open = Path.open

    def _raise_on_write(self: Path, mode: str = "r", **kwargs: object) -> object:
        if "w" in mode:
            raise OSError("permission denied")
        return _real_open(self, mode, **kwargs)

    with (
        patch("pathlib.Path.open", _raise_on_write),
        pytest.raises(WorkflowParseError, match="permission denied"),
    ):
        update_workflow_pins(
            wf,
            [PinReplacement("actions/checkout", "v3", "v4", line=1)],
        )


def test_update_workflow_pins_load_oserror_raises_parse_error(
    tmp_path: Path,
) -> None:
    """OSError on load (file vanished) must be caught and re-raised as WorkflowParseError."""
    missing = tmp_path / "nonexistent.yml"
    with pytest.raises(WorkflowParseError):
        update_workflow_pins(
            missing,
            [PinReplacement("actions/checkout", "v3", "v4", line=1)],
        )
