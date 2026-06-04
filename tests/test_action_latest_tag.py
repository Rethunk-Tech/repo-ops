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

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
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

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
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

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.run", return_value=tags_proc),
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


def test_shared_tag_cache_resolves_action_once_across_repos(tmp_path: Path) -> None:
    """A shared tag_cache means the GitHub API is called only once for the same action.

    Regression for issue #31: scan_actions used to create a fresh dict per call,
    so ``actions/checkout`` was re-fetched from the GitHub API for every repo in
    a multi-repo run.  With a caller-supplied cache the resolver is invoked once.
    """
    wf_content = "jobs:\n  ci:\n    steps:\n      - uses: actions/checkout@v4.2.1\n"

    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    for repo in (repo1, repo2):
        wf_dir = repo / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(wf_content, encoding="utf-8")

    tags_proc = _make_tags_proc("v4", "v4.2.1", "v4.2.2")

    shared_cache: dict[str, dict | None] = {}

    gh_call_count = 0

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> MagicMock:
        nonlocal gh_call_count
        if cmd and cmd[0] == "gh":
            gh_call_count += 1
            return tags_proc
        # actionlint: report success so it doesn't block pin scanning.
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch(
            "repo_ops_upgrade.actions.shutil.which",
            side_effect=lambda name: "/usr/bin/" + name,
        ),
        patch("repo_ops_upgrade.actions.run", side_effect=fake_run),
    ):
        scan_actions(repo1, apply=False, tag_cache=shared_cache)
        scan_actions(repo2, apply=False, tag_cache=shared_cache)

    # The gh API should have been called exactly once — cache hit on the second repo.
    assert gh_call_count == 1
