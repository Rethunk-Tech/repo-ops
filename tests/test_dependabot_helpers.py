"""Coverage for repo_ops_dependabot.dependabot URL parsing, gh-absent branches,
and the lockfile text-search scan."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_ops_dependabot import dependabot


def test_owner_repo_ssh_https_and_trailing_slash() -> None:
    assert dependabot._owner_repo("git@github.com:owner/repo.git") == "owner/repo"
    assert dependabot._owner_repo("https://github.com/owner/repo") == "owner/repo"
    assert dependabot._owner_repo("https://github.com/owner/repo/") == "owner/repo"
    assert dependabot._owner_repo("https://gitlab.com/owner/repo") is None


def test_gh_absent_short_circuits_all_gh_helpers() -> None:
    with patch.object(dependabot.shutil, "which", return_value=None):
        prs, err = dependabot._gh_list_dependabot_prs("owner/repo")
        assert prs == []
        assert err == "gh not on PATH"

        body, body_err = dependabot._gh_pr_body("owner/repo", 42)
        assert body == ""
        assert body_err == "gh not on PATH"

        close_err = dependabot._gh_close_pr("owner/repo", 42, "closing")
        assert close_err == "gh not on PATH"


def test_lockfile_versions_scans_package_lock_json(tmp_path: Path) -> None:
    """_lockfile_versions walks the tree and parses a real package-lock.json
    (v1-style 'dependencies' map) into a name@version set."""
    lock = {
        "name": "demo",
        "lockfileVersion": 1,
        "dependencies": {
            "foo": {"version": "1.2.3"},
            "bar": {"version": "4.5.6"},
        },
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")

    versions = dependabot._lockfile_versions(str(tmp_path))
    assert "foo@1.2.3" in versions
    assert "bar@4.5.6" in versions
