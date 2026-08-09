"""Tests for org allowed-actions policy audit (REPO_OPS_ACTIONS_ORG_POLICY=1).

Fleet incident: two GitHub orgs had allowed_actions=local_only, silently
startup-failing ALL workflows that use actions/checkout (0-second failures).
This feature detects that class of misconfiguration during the Actions scan.

Opt-in: REPO_OPS_ACTIONS_ORG_POLICY=1  (default off — costs extra gh api calls).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from repo_ops_upgrade.actions import (
    _action_policy_violation,
    _action_policy_violation_local_only,
    _derive_owner,
    _fetch_org_policy_cached,
    _read_policy_level_env,
    scan_actions,
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


def _gh_proc(payload: object, returncode: int = 0) -> MagicMock:
    return MagicMock(
        returncode=returncode,
        stdout=json.dumps(payload) if returncode == 0 else "",
        stderr="",
    )


# ---------------------------------------------------------------------------
# _derive_owner: parse owner from remote URL
# ---------------------------------------------------------------------------


def test_derive_owner_https(tmp_path: Path) -> None:
    """HTTPS github.com remote → owner extracted."""
    proc = MagicMock(
        returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
    )
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", return_value=proc),
    ):
        assert _derive_owner(tmp_path) == "myorg"


def test_derive_owner_ssh(tmp_path: Path) -> None:
    """SSH github.com remote → owner extracted."""
    proc = MagicMock(
        returncode=0, stdout="git@github.com:myorg/myrepo.git\n", stderr=""
    )
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", return_value=proc),
    ):
        assert _derive_owner(tmp_path) == "myorg"


def test_derive_owner_non_github_returns_none(tmp_path: Path) -> None:
    """Non-github remote → None (skip silently)."""
    proc = MagicMock(
        returncode=0, stdout="https://gitlab.com/myorg/myrepo.git\n", stderr=""
    )
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", return_value=proc),
    ):
        assert _derive_owner(tmp_path) is None


def test_derive_owner_git_absent_returns_none(tmp_path: Path) -> None:
    """git not on PATH → None."""
    with patch("repo_ops_upgrade.actions.shutil.which", return_value=None):
        assert _derive_owner(tmp_path) is None


def test_derive_owner_git_failure_returns_none(tmp_path: Path) -> None:
    """git config exits non-zero → None."""
    proc = MagicMock(returncode=1, stdout="", stderr="error")
    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", return_value=proc),
    ):
        assert _derive_owner(tmp_path) is None


# ---------------------------------------------------------------------------
# _fetch_org_policy_cached: caching behaviour
# ---------------------------------------------------------------------------


def test_fetch_policy_cached_caches_result(tmp_path: Path) -> None:
    """Policy is fetched once per owner; cache stores and returns on hit."""
    store: dict = {}
    permissions_payload = {"enabled": True, "allowed_actions": "all"}
    call_count = [0]

    def fake_run(cmd: list, **kw: object) -> MagicMock:
        call_count[0] += 1
        if "selected-actions" in cmd[-1]:
            return MagicMock(returncode=404, stdout="", stderr="")
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

    def fake_run(cmd: list, **kw: object) -> MagicMock:
        call_count[0] += 1
        return MagicMock(returncode=1, stdout="", stderr="Not Found")

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

    def fake_run(cmd: list, **kw: object) -> MagicMock:
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
    assert _action_policy_violation_local_only("myorg/some-action", p, "myorg") is None


def test_policy_local_only_cross_org_blocked() -> None:
    p = _policy(allowed_actions="local_only")
    v = _action_policy_violation_local_only("actions/checkout", p, "myorg")
    assert v is not None
    assert "local_only" in v


# ---------------------------------------------------------------------------
# scan_actions: env-off default does zero gh calls
# ---------------------------------------------------------------------------


def test_scan_no_gh_calls_when_env_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (REPO_OPS_ACTIONS_ORG_POLICY unset) → zero gh policy calls."""
    monkeypatch.delenv("REPO_OPS_ACTIONS_ORG_POLICY", raising=False)
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {}
    gh_calls: list[list] = []

    def fake_subprocess_run(cmd: list, **kw: object) -> MagicMock:
        if "gh" in cmd[0]:
            gh_calls.append(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/gh"),
        patch(
            "repo_ops_upgrade.actions.subprocess.run", side_effect=fake_subprocess_run
        ),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_calls = [c for c in gh_calls if "orgs" in " ".join(c)]
    assert policy_calls == [], f"expected zero policy gh calls, got: {policy_calls}"
    policy_rows = [b for b in bumps if b.status == "policy"]
    assert policy_rows == []


# ---------------------------------------------------------------------------
# scan_actions: enabled=false → every action flagged
# ---------------------------------------------------------------------------


def test_scan_policy_enabled_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enabled=false in org policy → both actions in the workflow flagged as policy."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": False,
            "allowed_actions": "all",
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    assert len(policy_rows) == 2, [b.action for b in policy_rows]
    actions_flagged = {b.action for b in policy_rows}
    assert "actions/checkout" in actions_flagged
    assert "softprops/action-gh-release" in actions_flagged
    for row in policy_rows:
        assert "disabled" in (row.detail or "")


# ---------------------------------------------------------------------------
# scan_actions: local_only mode
# ---------------------------------------------------------------------------


def test_scan_policy_local_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """local_only: same-org action allowed, cross-org flagged, path-local allowed."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _LOCAL_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "local_only",
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    flagged_actions = {b.action for b in policy_rows}
    # actions/checkout is cross-org → flagged
    assert "actions/checkout" in flagged_actions
    # myorg/internal-action is same-org → allowed
    assert "myorg/internal-action" not in flagged_actions
    # ./local-action is path-local → always allowed
    assert "./local-action" not in flagged_actions
    for row in policy_rows:
        assert "local_only" in (row.detail or "")


# ---------------------------------------------------------------------------
# scan_actions: selected mode — github_owned_allowed + patterns
# ---------------------------------------------------------------------------


def test_scan_policy_selected_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """selected: github-owned allowed, patterns matched, others flagged."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)  # actions/checkout@v4 + softprops/action-gh-release@v1
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "selected",
            "github_owned_allowed": True,  # actions/* and github/* allowed
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    flagged = {b.action for b in policy_rows}
    # actions/checkout is github-owned → allowed
    assert "actions/checkout" not in flagged
    # softprops/action-gh-release is not github-owned, no pattern match → flagged
    assert "softprops/action-gh-release" in flagged


def test_scan_policy_selected_pattern_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """selected: softprops/* pattern allows the action."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "selected",
            "github_owned_allowed": True,
            "verified_allowed": False,
            "patterns_allowed": ["softprops/*"],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    assert policy_rows == [], [b.action for b in policy_rows]


# ---------------------------------------------------------------------------
# scan_actions: all mode — nothing flagged
# ---------------------------------------------------------------------------


def test_scan_policy_all_flags_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allowed_actions=all → no policy violations emitted."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "all",
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    assert policy_rows == []


# ---------------------------------------------------------------------------
# scan_actions: user account (owner not in cache, 404) → no policy rows
# ---------------------------------------------------------------------------


def test_scan_policy_user_account_404_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User account (404 from gh api orgs/...) → skip silently, zero policy rows."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {}  # empty — will trigger real fetch

    gh_calls: list = []

    def fake_subprocess_run(cmd: list, **kw: object) -> MagicMock:
        if "git" in cmd[0]:
            return MagicMock(
                returncode=0, stdout="https://github.com/personal/repo.git\n", stderr=""
            )
        if "gh" in cmd[0]:
            gh_calls.append(cmd)
            # Simulate 404 for user account
            return MagicMock(returncode=1, stdout="", stderr="Not Found")
        return MagicMock(returncode=0, stdout="", stderr="")

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch(
            "repo_ops_upgrade.actions.subprocess.run", side_effect=fake_subprocess_run
        ),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    assert policy_rows == []
    # gh was called once (for the permissions endpoint), returned 404 → no further calls
    assert len(gh_calls) == 1


# ---------------------------------------------------------------------------
# scan_actions: gh absent → no policy rows
# ---------------------------------------------------------------------------


def test_scan_policy_gh_missing_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh not on PATH → policy fetch skipped, zero policy rows."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {}

    def fake_which(name: str) -> str | None:
        if name == "gh":
            return None
        return f"/usr/bin/{name}"

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/repo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=fake_which),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", return_value=None),
    ):
        bumps, _ = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    policy_rows = [b for b in bumps if b.status == "policy"]
    assert policy_rows == []


# ---------------------------------------------------------------------------
# scan_actions: policy rows are report-only (do not change exit code)
#   and do not interfere with normal pin bumps
# ---------------------------------------------------------------------------


def test_scan_policy_rows_report_only_and_pin_bumps_still_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Policy rows have status='policy'; normal pin-bump rows still produced."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    _wf(tmp_path, _CHECKOUT_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "local_only",
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    def fake_tag_info(action: str, **kw: object) -> dict | None:
        if action == "actions/checkout":
            return {
                "bare_majors": {4},
                "max_major": 4,
                "all_tags": {"v4"},
                "sha_to_tags": {},
            }
        return None

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch("repo_ops_upgrade.actions.action_tag_info", side_effect=fake_tag_info),
    ):
        bumps, scanned = scan_actions(
            tmp_path, apply=False, lint_level="off", org_policy_cache=policy_store
        )

    assert scanned == 1
    policy_rows = [b for b in bumps if b.status == "policy"]
    # Both actions in _CHECKOUT_WF are cross-org → flagged
    assert len(policy_rows) == 2
    # status is "policy", not "fail" — no exit-code impact
    assert all(b.status == "policy" for b in policy_rows)


def test_policy_level_env_defaults_to_report_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("REPO_OPS_ACTIONS_POLICY_LEVEL", raising=False)
    assert _read_policy_level_env() == "report"

    monkeypatch.setenv("REPO_OPS_ACTIONS_POLICY_LEVEL", "unknown")
    assert _read_policy_level_env() == "report"
    assert "defaulting to 'report'" in capsys.readouterr().err


def test_scan_policy_blocked_skips_pin_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """block: violating action keeps its pin and emits policy-blocked."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    wf = _wf(tmp_path, _BUMP_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "local_only",
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }
    before = wf.read_text(encoding="utf-8")

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info",
            return_value={
                "bare_majors": {1},
                "max_major": 1,
                "all_tags": {"v1"},
                "sha_to_tags": {},
            },
        ),
    ):
        bumps, _ = scan_actions(
            tmp_path,
            apply=True,
            lint_level="off",
            policy_level="block",
            org_policy_cache=policy_store,
        )

    assert wf.read_text(encoding="utf-8") == before
    blocked_rows = [b for b in bumps if b.status == "policy-blocked"]
    assert len(blocked_rows) == 1
    assert blocked_rows[0].action == "softprops/action-gh-release"
    assert all(b.status != "ok" for b in bumps)


def test_scan_policy_warn_applies_pin_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """warn: violating action is rewritten and emits a warn row."""
    monkeypatch.setenv("REPO_OPS_ACTIONS_ORG_POLICY", "1")
    wf = _wf(tmp_path, _BUMP_WF)
    policy_store: dict = {
        "myorg": {
            "enabled": True,
            "allowed_actions": "local_only",
            "github_owned_allowed": False,
            "verified_allowed": False,
            "patterns_allowed": [],
        }
    }

    def fake_git_run(cmd: list, **kw: object) -> MagicMock:
        return MagicMock(
            returncode=0, stdout="https://github.com/myorg/myrepo.git\n", stderr=""
        )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", return_value="/usr/bin/git"),
        patch("repo_ops_upgrade.actions.subprocess.run", side_effect=fake_git_run),
        patch(
            "repo_ops_upgrade.actions.action_tag_info",
            return_value={
                "bare_majors": {1},
                "max_major": 1,
                "all_tags": {"v1"},
                "sha_to_tags": {},
            },
        ),
    ):
        bumps, _ = scan_actions(
            tmp_path,
            apply=True,
            lint_level="off",
            policy_level="warn",
            org_policy_cache=policy_store,
        )

    assert "@v1\n" in wf.read_text(encoding="utf-8")
    warn_rows = [b for b in bumps if b.status == "warn"]
    assert len(warn_rows) == 1
    assert warn_rows[0].action == "softprops/action-gh-release"


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
