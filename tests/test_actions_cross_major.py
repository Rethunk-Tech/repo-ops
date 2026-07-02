"""Cross-major GitHub Actions bumps under latest mode (issue #106)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_models import RepoBundle
from repo_ops_upgrade.actions import scan_actions


def _make_tags_proc(*tags: str) -> MagicMock:
    """Mock process returning newline-joined tag names (name-only form)."""
    return MagicMock(returncode=0, stdout="\n".join(tags) + "\n", stderr="")


def _write_workflow(tmp_path: Path, ref: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{ref}\n",
        encoding="utf-8",
    )
    return wf


def _scan(tmp_path: Path, tags_proc: MagicMock, **kwargs):
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        return scan_actions(tmp_path, **kwargs)


def test_latest_applies_cross_major_floating(tmp_path: Path) -> None:
    """v4 floating + latest mode → rewrite to v6 (bare major exists); file updated."""
    wf = _write_workflow(tmp_path, "v4")
    tags_proc = _make_tags_proc("v4", "v5", "v5.1.0", "v6", "v6.0.1")

    bumps, scanned = _scan(tmp_path, tags_proc, apply=True, upgrade_mode="latest")

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].pin == "v4"
    assert checkout[0].latest == "v6"
    detail = (checkout[0].detail or "").lower()
    assert "report only" not in detail
    assert "not auto-applied" not in detail
    assert "actions/checkout@v6" in wf.read_text(encoding="utf-8")

    # Counts as an actionable bump, not behind-latest.
    summary = RepoBundle(git_repo=tmp_path, label="r", actions=bumps).actions_summary()
    assert summary["bumps"] == 1
    assert summary["behind_latest"] == 0


def test_latest_exact_style_targets_highest_exact(tmp_path: Path) -> None:
    """v4 floating + latest + tag_style=exact → rewrite to highest v6.M.P."""
    wf = _write_workflow(tmp_path, "v4")
    tags_proc = _make_tags_proc("v4", "v6", "v6.0.1", "v6.1.0")

    bumps, _ = _scan(
        tmp_path, tags_proc, apply=True, upgrade_mode="latest", tag_style="exact"
    )

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == "v6.1.0"
    assert "actions/checkout@v6.1.0" in wf.read_text(encoding="utf-8")


def test_latest_exact_pin_jumps_to_newest_major(tmp_path: Path) -> None:
    """v4.2.1 + latest mode jumps straight to v6, not normalize-to-v4."""
    wf = _write_workflow(tmp_path, "v4.2.1")
    tags_proc = _make_tags_proc("v4", "v4.2.1", "v6", "v6.0.1")

    bumps, _ = _scan(tmp_path, tags_proc, apply=True, upgrade_mode="latest")

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == "v6"
    assert "actions/checkout@v6" in wf.read_text(encoding="utf-8")


def test_latest_falls_back_to_normalize_when_no_bare_newest(tmp_path: Path) -> None:
    """v4.2.1 + latest + tag_style=major but no bare v6 → normalize to v4 (no regress)."""
    wf = _write_workflow(tmp_path, "v4.2.1")
    # Newest major (6) has only an exact tag, no bare v6 — major style can't target it.
    tags_proc = _make_tags_proc("v4", "v4.2.1", "v6.0.1")

    bumps, _ = _scan(tmp_path, tags_proc, apply=True, upgrade_mode="latest")

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == "v4"
    assert "normalize to floating major v4" in (checkout[0].detail or "")
    assert "actions/checkout@v4" in wf.read_text(encoding="utf-8")
    assert "actions/checkout@v4.2.1" not in wf.read_text(encoding="utf-8")


def test_latest_floating_no_bare_newest_stays_report_only(tmp_path: Path) -> None:
    """v4 floating + latest + tag_style=major but no bare v6 → report only; unchanged."""
    wf = _write_workflow(tmp_path, "v4")
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc("v4", "v6.0.1")

    bumps, _ = _scan(tmp_path, tags_proc, apply=True, upgrade_mode="latest")

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == "v6"
    assert "report only" in (checkout[0].detail or "").lower()
    assert wf.read_text(encoding="utf-8") == original


def test_default_mode_keeps_cross_major_report_only(tmp_path: Path) -> None:
    """Default upgrade_mode='normalize': cross-major stays report-only (file unchanged)."""
    wf = _write_workflow(tmp_path, "v4")
    original = wf.read_text(encoding="utf-8")
    tags_proc = _make_tags_proc("v4", "v6", "v6.0.1")

    bumps, _ = _scan(tmp_path, tags_proc, apply=True)  # default normalize

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout[0].latest == "v6"
    assert "not auto-applied" in (checkout[0].detail or "").lower()
    assert wf.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Cross-major SHA-pin refresh (#138)
# Extends coverage for the keep-sha + latest interaction from the cross-major
# perspective (SHA-specific depth is in test_actions_sha_refresh.py).
# ---------------------------------------------------------------------------

_SHA_V4 = "aabbccddeeff00112233445566778899aabbccdd"
_SHA_V6 = "00112233445566778899aabbccddeeff00112233"


def test_sha_pin_cross_major_refresh_under_latest(tmp_path: Path) -> None:
    """SHA-pinned v4 action advances to v6's digest when --latest + --actions-refresh-sha."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA_V4}  # v4\n",
        encoding="utf-8",
    )
    tags_proc = MagicMock(
        returncode=0,
        stdout=(f"v6.0.1 {_SHA_V6}\nv6 {_SHA_V6}\nv4.2.1 {_SHA_V4}\nv4 {_SHA_V4}\n"),
        stderr="",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, scanned = scan_actions(
            tmp_path,
            apply=True,
            keep_sha=True,
            refresh_sha=True,
            upgrade_mode="latest",
        )

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    assert b.pin == _SHA_V4
    assert b.latest == _SHA_V6
    assert "cross-major" in (b.detail or "").lower()

    text = wf.read_text(encoding="utf-8")
    assert _SHA_V6 in text, "v6 digest not written"
    assert _SHA_V4 not in text, "old v4 digest still present"
    assert "# v6" in text, "comment not updated to v6"
    assert "# v4" not in text, "old comment still present"


def test_sha_pin_cross_major_refresh_normalize_stays_in_current(tmp_path: Path) -> None:
    """normalize mode: SHA-pinned v4 stays in v4 even when v6 exists."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    _SHA_V4_OLD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    _SHA_V4_NEW = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    wf.write_text(
        f"jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@{_SHA_V4_OLD}  # v4\n",
        encoding="utf-8",
    )
    tags_proc = MagicMock(
        returncode=0,
        stdout=(f"v6.0.1 {_SHA_V6}\nv4.3.0 {_SHA_V4_NEW}\nv4.2.1 {_SHA_V4_OLD}\n"),
        stderr="",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
    ):
        bumps, _ = scan_actions(
            tmp_path,
            apply=True,
            keep_sha=True,
            refresh_sha=True,
            upgrade_mode="normalize",
        )

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    b = checkout[0]
    # Must advance within v4 (to v4.3.0's SHA), not jump to v6.
    assert b.latest == _SHA_V4_NEW
    assert "cross-major" not in (b.detail or "").lower()
    text = wf.read_text(encoding="utf-8")
    assert "# v4" in text
    assert "# v6" not in text
