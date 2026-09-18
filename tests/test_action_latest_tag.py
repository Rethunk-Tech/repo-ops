"""GitHub Actions tag-info helper and floating-major pin policy."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import action_tag_info, scan_actions


def _make_tags_proc(*tags: str) -> MagicMock:
    """Build a mock process that returns a newline-joined list of tag names."""
    return MagicMock(
        returncode=0, stdout="".join(f"{t} {t.encode().hex():0<40.40}\n" for t in tags), stderr=""
    )


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

    Without a caller-supplied cache, scan_actions builds a fresh dict per call
    and re-fetches ``actions/checkout`` from the GitHub API for every repo in a
    multi-repo run. A shared cache invokes the resolver once.
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
