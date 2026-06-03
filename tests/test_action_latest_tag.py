"""GitHub Actions tag-info helper and floating-major pin policy (issue #20)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import action_tag_info, scan_actions


def _make_tags_proc(*tags: str) -> MagicMock:
    """Build a mock process that returns a newline-joined list of tag names."""
    return MagicMock(returncode=0, stdout="\n".join(tags) + "\n", stderr="")


def test_collapse_exact_to_floating_major(tmp_path: Path) -> None:
    """v4.2.1 with bare v4 tag upstream → normalize to v4; file updated on apply."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v4.2.1\n",
        encoding="utf-8",
    )
    tags_proc = _make_tags_proc("v4", "v4.2.1", "v4.2.2")

    with patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"), patch(
        "repo_ops_upgrade.actions.run", return_value=tags_proc
    ):
        bumps, scanned = scan_actions(tmp_path, apply=True)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].pin == "v4.2.1"
    assert checkout[0].latest == "v4"
    assert "normalize to floating major v4" in (checkout[0].detail or "")
    assert "report only" not in (checkout[0].detail or "").lower()
    assert "actions/checkout@v4" in wf.read_text(encoding="utf-8")
    assert "actions/checkout@v4.2.1" not in wf.read_text(encoding="utf-8")


def test_cross_major_is_report_only_no_replacement(tmp_path: Path) -> None:
    """v4 (already bare floating major) with v6 upstream → report only; file unchanged."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    original = "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v4\n"
    wf.write_text(original, encoding="utf-8")
    tags_proc = _make_tags_proc("v4", "v5", "v5.1.0", "v6", "v6.0.1")

    with patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"), patch(
        "repo_ops_upgrade.actions.run", return_value=tags_proc
    ):
        bumps, scanned = scan_actions(tmp_path, apply=True)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].pin == "v4"
    assert checkout[0].latest == "v6"
    assert "report only" in (checkout[0].detail or "").lower()
    # File must be unchanged — no cross-major replacement applied.
    assert wf.read_text(encoding="utf-8") == original


def test_bare_major_up_to_date_emits_nothing(tmp_path: Path) -> None:
    """v3 with only v3* tags and max_major==3 → no ActionBump entry at all."""
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(
        "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v3\n",
        encoding="utf-8",
    )
    tags_proc = _make_tags_proc("v3", "v3.6.0", "v3.5.0")

    with patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"), patch(
        "repo_ops_upgrade.actions.run", return_value=tags_proc
    ):
        bumps, scanned = scan_actions(tmp_path, apply=False)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout == []


def test_gh_absent_skips_pin(tmp_path: Path) -> None:
    """When gh is not on PATH, action_tag_info returns None and pin is skipped."""
    from repo_ops_upgrade import actions as actions_mod

    actions_mod._TAG_CACHE.clear()

    with patch("repo_ops_upgrade.actions.shutil.which", return_value=None):
        result = action_tag_info("actions/checkout")
    assert result is None

    # scan_actions also skips (no bump emitted for non-SHA pins).
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    with patch("repo_ops_upgrade.actions.shutil.which", return_value=None):
        bumps, _ = scan_actions(tmp_path, apply=False)

    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert checkout == []
