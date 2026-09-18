"""Tests for the org allowed-actions policy audit.

Fleet incident: two GitHub orgs had allowed_actions=local_only, silently
startup-failing ALL workflows that use actions/checkout (0-second failures).
This feature detects that class of misconfiguration during the Actions scan.

Runs whenever gh is authenticated; reports only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_upgrade.actions import (
    _action_policy_violation,
    _derive_owner,
    _fetch_org_policy_cached,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wf(tmp_path: Path, content: str) -> Path:
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return wf


_CHECKOUT_WF = """\
name: CI
on: push
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: softprops/action-gh-release@v1
"""

_LOCAL_WF = """\
name: CI
on: push
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: ./local-action
      - uses: myorg/internal-action@v1
      - uses: actions/checkout@v4
"""

_BUMP_WF = """\
name: CI
on: push
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - uses: softprops/action-gh-release@v1.0.0
"""


def _gh_proc(payload: object, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        [],
        returncode,
        stdout=json.dumps(payload) if returncode == 0 else "",
        stderr="",
    )


# ---------------------------------------------------------------------------
# _derive_owner: parse owner from remote URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote_url", "expected_owner"),
    [
        ("https://github.com/myorg/myrepo.git", "myorg"),  # HTTPS
        ("git@github.com:myorg/myrepo.git", "myorg"),  # SSH
        ("ssh://git@github.com/myorg/myrepo.git", "myorg"),  # ssh:// URL
        ("https://gitlab.com/myorg/myrepo.git", None),  # non-github → skip silently
    ],
)
def test_derive_owner_from_remote(
    tmp_path: Path, init_git_repo, remote_url: str, expected_owner: str | None
) -> None:
    init_git_repo(tmp_path)
    subprocess.run(["git", "remote", "add", "upstream", remote_url], cwd=tmp_path, check=True)
    assert _derive_owner(tmp_path) == expected_owner


def test_derive_owner_without_remote_returns_none(tmp_path: Path, init_git_repo) -> None:
    init_git_repo(tmp_path)
    assert _derive_owner(tmp_path) is None


# ---------------------------------------------------------------------------
# _fetch_org_policy_cached: caching behaviour
# ---------------------------------------------------------------------------


def test_fetch_policy_cached_caches_result(tmp_path: Path) -> None:
    """Policy is fetched once per owner; cache stores and returns on hit."""
    store: dict = {}
    permissions_payload = {"enabled": True, "allowed_actions": "all"}
    call_count = [0]

    def fake_run(cmd: list, **kw: object) -> subprocess.CompletedProcess[str]:
        call_count[0] += 1
        if "selected-actions" in cmd[-1]:
            return subprocess.CompletedProcess([], 404, stdout="", stderr="")
        return _gh_proc(permissions_payload)

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_run),
    ):
        result1 = _fetch_org_policy_cached("myorg", store)
        result2 = _fetch_org_policy_cached("myorg", store)  # cache hit

    assert result1 is not None
    assert result1["allowed_actions"] == "all"
    assert result2 is result1  # same object from cache
    assert call_count[0] == 1  # gh called only once


def test_fetch_policy_404_returns_none_and_caches() -> None:
    """404 (user account) → None, cached so no second call."""
    store: dict = {}
    call_count = [0]

    def fake_run(cmd: list, **kw: object) -> subprocess.CompletedProcess[str]:
        call_count[0] += 1
        return subprocess.CompletedProcess([], 1, stdout="", stderr="Not Found")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_run),
    ):
        r1 = _fetch_org_policy_cached("personal-user", store)
        r2 = _fetch_org_policy_cached("personal-user", store)

    assert r1 is None
    assert r2 is None
    assert call_count[0] == 1  # not retried


def test_fetch_policy_gh_absent_returns_none() -> None:
    """gh not on PATH → None without any subprocess call."""
    store: dict = {}
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value=None),
        patch("repo_ops_upgrade.actions.subprocess.run") as mock_run,
    ):
        result = _fetch_org_policy_cached("myorg", store)

    assert result is None
    mock_run.assert_not_called()


def test_fetch_policy_selected_fetches_detail() -> None:
    """allowed_actions=selected triggers a second gh api call for selected-actions."""
    store: dict = {}
    permissions_payload = {"enabled": True, "allowed_actions": "selected"}
    selected_payload = {
        "github_owned_allowed": True,
        "verified_allowed": False,
        "patterns_allowed": ["myorg/*"],
    }

    def fake_run(cmd: list, **kw: object) -> subprocess.CompletedProcess[str]:
        if "selected-actions" in cmd[-1]:
            return _gh_proc(selected_payload)
        return _gh_proc(permissions_payload)

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_run),
    ):
        policy = _fetch_org_policy_cached("myorg", store)

    assert policy is not None
    assert policy["allowed_actions"] == "selected"
    assert policy["github_owned_allowed"] is True
    assert policy["patterns_allowed"] == ["myorg/*"]


# ---------------------------------------------------------------------------
# _action_policy_violation: evaluation logic
# ---------------------------------------------------------------------------


def _policy(
    *,
    enabled: bool = True,
    allowed_actions: str = "all",
    github_owned_allowed: bool = False,
    patterns_allowed: list[str] | None = None,
) -> dict:
    return {
        "enabled": enabled,
        "allowed_actions": allowed_actions,
        "github_owned_allowed": github_owned_allowed,
        "verified_allowed": False,
        "patterns_allowed": patterns_allowed or [],
    }


def test_policy_all_allows_everything() -> None:
    p = _policy(allowed_actions="all")
    assert _action_policy_violation("actions/checkout", p) is None
    assert _action_policy_violation("softprops/action-gh-release", p) is None


def test_policy_disabled_blocks_everything() -> None:
    p = _policy(enabled=False, allowed_actions="all")
    v = _action_policy_violation("actions/checkout", p)
    assert v is not None
    assert "disabled" in v


def test_policy_selected_github_owned_allowed() -> None:
    p = _policy(allowed_actions="selected", github_owned_allowed=True)
    assert _action_policy_violation("actions/checkout", p) is None
    assert _action_policy_violation("github/super-linter", p) is None


def test_policy_selected_github_owned_blocked() -> None:
    p = _policy(allowed_actions="selected", github_owned_allowed=False)
    v = _action_policy_violation("actions/checkout", p)
    assert v is not None
    assert "not in allowlist" in v


def test_policy_selected_pattern_match() -> None:
    p = _policy(
        allowed_actions="selected",
        github_owned_allowed=False,
        patterns_allowed=["myorg/*", "actions/checkout"],
    )
    assert _action_policy_violation("myorg/internal-action", p) is None
    assert _action_policy_violation("actions/checkout", p) is None


def test_policy_selected_pattern_no_match() -> None:
    p = _policy(
        allowed_actions="selected",
        patterns_allowed=["myorg/*"],
    )
    v = _action_policy_violation("softprops/action-gh-release", p)
    assert v is not None
    assert "not in allowlist" in v


def test_policy_path_local_always_allowed() -> None:
    p = _policy(allowed_actions="local_only")
    assert _action_policy_violation("./my-local-action", p) is None


def test_policy_local_only_same_org_allowed() -> None:
    p = _policy(allowed_actions="local_only")
    assert _action_policy_violation("myorg/some-action", p, "myorg") is None


def test_policy_local_only_cross_org_blocked() -> None:
    p = _policy(allowed_actions="local_only")
    v = _action_policy_violation("actions/checkout", p, "myorg")
    assert v is not None
    assert "local_only" in v


# ---------------------------------------------------------------------------
# scan_actions: policy rows are report-only (do not change exit code)
#   and do not interfere with normal pin bumps
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# RepoBundle.actions_summary: policy_violations count
# ---------------------------------------------------------------------------


def test_actions_summary_policy_violations_counted() -> None:
    """actions_summary() includes policy_violations key for policy-status rows."""
    from repo_ops_models import ActionBump, RepoBundle

    bundle = RepoBundle(git_repo=Path("/fake"), label="fake")
    bundle.actions = [
        ActionBump(
            workflow="ci.yml",
            action="softprops/action-gh-release",
            pin="v1",
            latest="v1",
            status="policy",
            detail="org policy (local_only): not allowed",
        ),
        ActionBump(
            workflow="ci.yml",
            action="actions/checkout",
            pin="v3",
            latest="v4",
            status="ok",
            detail="normalize to floating major v3",
        ),
    ]
    bundle.actions_workflows_scanned = 1
    summary = bundle.actions_summary()
    assert summary["policy_violations"] == 1
    # v3 → v4 with no "report only" detail → counted as a bump
    assert summary["bumps"] == 1


def test_actions_summary_policy_does_not_count_as_failure() -> None:
    """Policy rows do not increment the failures counter."""
    from repo_ops_models import ActionBump, RepoBundle

    bundle = RepoBundle(git_repo=Path("/fake"), label="fake")
    bundle.actions = [
        ActionBump(
            workflow="ci.yml",
            action="myaction/foo",
            pin="v1",
            latest="v1",
            status="policy",
            detail="org policy (local_only): myaction/foo not allowed",
        ),
    ]
    bundle.actions_workflows_scanned = 1
    summary = bundle.actions_summary()
    assert summary["failures"] == 0
    assert summary["policy_violations"] == 1


@pytest.mark.parametrize("status", ["policy", "policy-blocked", "warn"])
def test_actions_summary_policy_statuses_are_not_bumps(status: str) -> None:
    """Policy-status rows count as violations, never actionable bumps."""
    from repo_ops_models import ActionBump, RepoBundle

    bundle = RepoBundle(git_repo=Path("/fake"), label="fake")
    bundle.actions = [
        ActionBump(
            workflow="ci.yml",
            action="myaction/foo",
            pin="v1",
            latest="v2",
            status=status,
            detail="org policy violation",
        ),
    ]

    summary = bundle.actions_summary()
    assert summary["policy_violations"] == 1
    assert summary["bumps"] == 0
