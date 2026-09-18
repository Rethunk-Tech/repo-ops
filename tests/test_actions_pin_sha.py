"""Tests for pinning a floating tag Action (@vN or @vN.M.P) to a commit SHA."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import scan_actions

_V7_0_1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_V7_0_0 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_V8_0_0 = "dddddddddddddddddddddddddddddddddddddddd"


def _make_tags_proc(*lines: str) -> MagicMock:
    return MagicMock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")


def _wf(tmp_path: Path, content: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return wf


def test_pin_sha_bare_major_tag_pins_to_highest_exact_release_no_prior_comment(
    tmp_path: Path,
) -> None:
    """@v7 with no comment at all -> SHA of v7.0.1, with a new # v7.0.1 comment added."""
    wf = _wf(tmp_path, "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v7\n")
    tags_proc = _make_tags_proc(
        f"v7.0.1 {_V7_0_1}",
        f"v7.0.0 {_V7_0_0}",
        f"v7 {_V7_0_1}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(tmp_path, apply=True)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1, bumps
    b = checkout[0]
    assert b.pin == "v7"
    assert b.latest == _V7_0_1
    assert "pin floating v7 to SHA" in (b.detail or "")

    text = wf.read_text(encoding="utf-8")
    assert f"@{_V7_0_1}" in text
    assert "@v7\n" not in text
    assert "# v7.0.1" in text


def test_pin_sha_exact_tag_pins_to_own_sha_and_replaces_no_comment(
    tmp_path: Path,
) -> None:
    """@v7.0.0 with an existing (different) comment -> SHA pin, comment replaced."""
    wf = _wf(
        tmp_path,
        "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v7.0.0  # stale note\n",
    )
    tags_proc = _make_tags_proc(f"v7.0.1 {_V7_0_1}", f"v7.0.0 {_V7_0_0}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    # Stays within v7 (the pin's own major); resolves to the highest exact
    # release in that major, matching refresh_sha's within-major behavior.
    assert checkout[0].latest == _V7_0_1

    text = wf.read_text(encoding="utf-8")
    assert f"@{_V7_0_1}" in text
    assert "# v7.0.1" in text
    assert "stale note" not in text


def test_pin_sha_crosses_majors(tmp_path: Path) -> None:
    wf = _wf(tmp_path, "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v7\n")
    tags_proc = _make_tags_proc(
        f"v8.0.0 {_V8_0_0}",
        f"v8 {_V8_0_0}",
        f"v7.0.1 {_V7_0_1}",
        f"v7 {_V7_0_1}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].latest == _V8_0_0

    text = wf.read_text(encoding="utf-8")
    assert f"@{_V8_0_0}" in text
    assert "# v8.0.0" in text


def test_pin_sha_no_exact_release_in_major_falls_back_to_tag_normalize(
    tmp_path: Path,
) -> None:
    """Only a bare v7 tag exists (no vN.M.P release) -> can't compute a digest;
    falls through to the existing tag-normalize path instead of crashing/no-op."""
    wf = _wf(
        tmp_path,
        "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v7.0.0\n",
    )
    tags_proc = _make_tags_proc(f"v7 {_V7_0_1}")  # no vN.M.P release at all

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    # No SHA digest available in-major -> stays tag-pinned (normalize path).
    assert "pin floating" not in (checkout[0].detail or "")
    text = wf.read_text(encoding="utf-8")
    assert _V7_0_1 not in text
