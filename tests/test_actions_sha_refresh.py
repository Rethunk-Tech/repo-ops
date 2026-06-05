"""Tests for SHA-pin digest refresh within a major (issue #109).

Keeps an action pinned by commit digest (supply-chain hardening) but advances
the SHA to the newest vN.M.P release within its current major — the same kind of
bump Dependabot makes when it security-pins by commit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import scan_actions

# v6.0.2 commit (the current pin) and v6.0.3 commit (the refresh target).
_OLD = "de0fac2e4500dabe0009e67214ff5f5447ce83dd"
_NEW = "11bd71901bbe5b1630ceea73d27597364c9af683"
_OTHER = "ffffffffffffffffffffffffffffffffffffffff"


def _make_tags_proc(*lines: str) -> MagicMock:
    return MagicMock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")


def _wf(tmp_path: Path, content: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return wf


def test_refresh_advances_digest_within_major_and_keeps_comment(tmp_path: Path) -> None:
    """# v6 SHA pin at v6.0.2 → digest rewritten to v6.0.3's commit; # v6 preserved."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    tags_proc = _make_tags_proc(
        f"v6.0.3 {_NEW}",
        f"v6.0.2 {_OLD}",
        f"v6.0.1 {_OTHER}",
        f"v6 {_OTHER}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(
            tmp_path, apply=True, keep_sha=True, refresh_sha=True
        )

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1, bumps
    b = checkout[0]
    assert b.pin == _OLD
    assert b.latest == _NEW
    assert "refresh SHA digest to v6.0.3" in (b.detail or "")
    assert "report only" not in (b.detail or "").lower()

    text = wf.read_text(encoding="utf-8")
    assert _NEW in text
    assert _OLD not in text
    assert "# v6" in text  # digest stays pinned; audit hint preserved


def test_refresh_resolves_major_via_tags_api_without_comment(tmp_path: Path) -> None:
    """No # vN comment: the major is resolved from the tags API, then refreshed."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}\n",
    )
    tags_proc = _make_tags_proc(
        f"v6.0.3 {_NEW}",
        f"v6.0.2 {_OLD}",  # current SHA → v6.0.2 → major 6
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].latest == _NEW
    assert _NEW in wf.read_text(encoding="utf-8")


def test_refresh_already_current_is_report_only(tmp_path: Path) -> None:
    """When the pin is already the newest in-major digest, nothing is rewritten."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_NEW}  # v6\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(
        f"v6.0.3 {_NEW}",  # newest in-major points at the current pin
        f"v6.0.2 {_OLD}",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True, refresh_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.latest == _NEW
    assert b.detail == "SHA-pinned; report only"
    assert wf.read_text(encoding="utf-8") == original


def test_refresh_off_by_default_keeps_report_only(tmp_path: Path) -> None:
    """Without refresh_sha, a SHA pin stays report-only even when a newer digest exists."""
    wf = _wf(
        tmp_path,
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_OLD}  # v6\n",
    )
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc(f"v6.0.3 {_NEW}", f"v6.0.2 {_OLD}")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(tmp_path, apply=True, keep_sha=True)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].detail == "SHA-pinned; report only"
    assert wf.read_text(encoding="utf-8") == original
