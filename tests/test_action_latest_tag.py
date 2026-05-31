"""GitHub Actions latest tag resolution via gh api (issue #20)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from repo_ops_upgrade.actions import action_latest_tag, scan_actions


def test_action_latest_tag_uses_gh_api_not_allowlist() -> None:
    release_proc = MagicMock(returncode=0, stdout="v6\n", stderr="")
    with patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"), patch(
        "repo_ops_upgrade.actions.run", return_value=release_proc
    ) as mock_run:
        assert action_latest_tag("actions/checkout") == "v6"
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][:3] == ["gh", "api", "repos/actions/checkout/releases/latest"]


def test_action_latest_tag_falls_back_to_tags() -> None:
    release_proc = MagicMock(returncode=1, stdout="", stderr="404")
    tags_proc = MagicMock(returncode=0, stdout="v6\n", stderr="")
    with patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"), patch(
        "repo_ops_upgrade.actions.run", side_effect=[release_proc, tags_proc]
    ):
        assert action_latest_tag("actions/checkout") == "v6"


def test_action_latest_tag_no_gh() -> None:
    with patch("repo_ops_upgrade.actions.shutil.which", return_value=None):
        assert action_latest_tag("actions/checkout") is None


def test_scan_actions_checkout_bump_when_api_newer(tmp_path: Path) -> None:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "ci.yml").write_text(
        "jobs:\n"
        "  ci:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    release_proc = MagicMock(returncode=0, stdout="v6\n", stderr="")

    with patch("repo_ops_upgrade.actions.shutil.which") as which_mock, patch(
        "repo_ops_upgrade.actions.run", return_value=release_proc
    ):
        which_mock.side_effect = lambda cmd: "/usr/bin/gh" if cmd == "gh" else None
        bumps, scanned = scan_actions(tmp_path, apply=False)

    assert scanned == 1
    checkout = [b for b in bumps if b.action == "actions/checkout"]
    assert len(checkout) == 1
    assert checkout[0].pin == "v4"
    assert checkout[0].latest == "v6"
