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
