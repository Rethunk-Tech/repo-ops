"""Tests for opt-in branch + PR workflow (--pr flag, pr.py module).

ALL network operations are mocked — no real git push or gh call is made.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest

from repo_ops_models import RepoBundle, Workspace, WorkspaceResult
from repo_ops_upgrade.pr import (
    PROutcome,
    branch_exists_local,
    compute_branch_name,
    create_or_checkout_branch,
    detect_default_branch,
    gh_available,
    gh_pr_exists,
    open_or_update_pr,
    push_branch,
    run_pr_flow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bundle(tmp_path: Path, *, label: str = "org/my-repo") -> RepoBundle:
    ws = Workspace(
        path=tmp_path,
        ecosystem="node",
        manager="npm",
        git_repo=tmp_path,
        label=label,
    )
    wr = WorkspaceResult(workspace=ws, status="ok")
    return RepoBundle(git_repo=tmp_path, label=label, workspaces=[wr])


# ---------------------------------------------------------------------------
# Branch-name determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "prefix", "expected"),
    [
        pytest.param(
            "org/my-repo", None, "repo-ops/deps/org-my-repo", id="slash_in_label"
        ),
        pytest.param("~/src/foo", None, "repo-ops/deps/src-foo", id="home_stripped"),
        pytest.param(
            "just-a-repo", None, "repo-ops/deps/just-a-repo", id="plain_label"
        ),
        pytest.param("org/repo", "bumps", "bumps/org-repo", id="custom_prefix"),
    ],
)
def test_branch_name(label: str, prefix: str | None, expected: str) -> None:
    kwargs = {"prefix": prefix} if prefix is not None else {}
    assert compute_branch_name(label, **kwargs) == expected


def test_branch_name_deterministic_same_label() -> None:
    """Same label always produces the same branch name (no timestamps)."""
    n1 = compute_branch_name("acme/backend")
    n2 = compute_branch_name("acme/backend")
    assert n1 == n2


def test_branch_name_truncated_at_60_plus_prefix() -> None:
    long_label = "a" * 80
    name = compute_branch_name(long_label, prefix="p")
    slug_part = name[len("p/") :]
    assert len(slug_part) <= 60


def test_branch_name_special_chars_slugified() -> None:
    name = compute_branch_name("org/repo name@v2")
    assert " " not in name
    assert "@" not in name


def test_branch_name_empty_after_strip_uses_unnamed() -> None:
    # Label collapses to nothing after stripping
    name = compute_branch_name("~/")
    assert name.endswith("/unnamed")


# ---------------------------------------------------------------------------
# gh_available
# ---------------------------------------------------------------------------


def test_gh_available_true_when_installed(make_proc) -> None:
    with patch(
        "subprocess.run", return_value=make_proc(stdout="gh version 2.0.0")
    ) as mock_run:
        result = gh_available()
    assert result is True
    mock_run.assert_called_once()


def test_gh_available_false_when_not_found() -> None:
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = gh_available()
    assert result is False


def test_gh_available_false_on_nonzero_exit(make_proc) -> None:
    with patch("subprocess.run", return_value=make_proc(returncode=1, stderr="err")):
        result = gh_available()
    assert result is False


# ---------------------------------------------------------------------------
# branch_exists_local
# ---------------------------------------------------------------------------


def test_branch_exists_local_true(tmp_path: Path, make_proc) -> None:
    with patch(
        "repo_ops_upgrade.pr.run_git", return_value=make_proc(stdout="abc123")
    ) as m:
        assert branch_exists_local(tmp_path, "repo-ops/deps/foo") is True
    m.assert_called_once_with(
        tmp_path, "rev-parse", "--verify", "refs/heads/repo-ops/deps/foo", timeout=10
    )


def test_branch_exists_local_false(tmp_path: Path, make_proc) -> None:
    with patch(
        "repo_ops_upgrade.pr.run_git",
        return_value=make_proc(returncode=1, stderr="err"),
    ):
        assert branch_exists_local(tmp_path, "repo-ops/deps/foo") is False


# ---------------------------------------------------------------------------
# create_or_checkout_branch
# ---------------------------------------------------------------------------


def test_create_or_checkout_creates_new_branch(tmp_path: Path, make_proc) -> None:
    """When branch does not exist locally, uses checkout -b."""
    branch = "repo-ops/deps/x"
    with patch("repo_ops_upgrade.pr.run_git") as mock_git:
        # First call: rev-parse (branch does NOT exist)
        # Second call: checkout -b
        mock_git.side_effect = [make_proc(returncode=1, stderr="err"), make_proc()]
        ok, err = create_or_checkout_branch(tmp_path, branch)
    assert ok is True
    assert err is None
    assert mock_git.call_args_list[1] == call(
        tmp_path, "checkout", "-b", branch, timeout=30
    )


def test_create_or_checkout_switches_existing_branch(tmp_path: Path, make_proc) -> None:
    """When branch already exists locally, uses checkout (no -b)."""
    branch = "repo-ops/deps/x"
    with patch("repo_ops_upgrade.pr.run_git") as mock_git:
        mock_git.side_effect = [
            make_proc(stdout="abc"),
            make_proc(),
        ]  # exists; checkout ok
        ok, _err = create_or_checkout_branch(tmp_path, branch)
    assert ok is True
    assert mock_git.call_args_list[1] == call(tmp_path, "checkout", branch, timeout=30)


def test_create_or_checkout_returns_error_on_fail(tmp_path: Path, make_proc) -> None:
    branch = "repo-ops/deps/x"
    with patch("repo_ops_upgrade.pr.run_git") as mock_git:
        mock_git.side_effect = [
            make_proc(returncode=1, stderr="err"),
            make_proc(returncode=1, stderr="checkout failed"),
        ]
        ok, err = create_or_checkout_branch(tmp_path, branch)
    assert ok is False
    assert err is not None


# ---------------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------------


def test_push_branch_success(tmp_path: Path, make_proc) -> None:
    with patch("repo_ops_upgrade.pr.run_git", return_value=make_proc()) as m:
        ok, err = push_branch(tmp_path, "repo-ops/deps/x")
    assert ok is True
    assert err is None
    m.assert_called_once_with(
        tmp_path, "push", "--set-upstream", "origin", "repo-ops/deps/x", timeout=120
    )


def test_push_branch_failure(tmp_path: Path, make_proc) -> None:
    with patch(
        "repo_ops_upgrade.pr.run_git",
        return_value=make_proc(returncode=1, stderr="remote rejected"),
    ):
        ok, err = push_branch(tmp_path, "repo-ops/deps/x")
    assert ok is False
    assert "remote rejected" in (err or "")


# ---------------------------------------------------------------------------
# gh_pr_exists
# ---------------------------------------------------------------------------


def test_gh_pr_exists_true(tmp_path: Path, make_proc) -> None:
    url = "https://github.com/org/repo/pull/42"
    with patch(
        "repo_ops_upgrade.pr._run_gh", return_value=make_proc(stdout=url + "\n")
    ):
        exists, found_url = gh_pr_exists(tmp_path, "repo-ops/deps/x")
    assert exists is True
    assert found_url == url


def test_gh_pr_exists_false_no_pr(tmp_path: Path, make_proc) -> None:
    with patch(
        "repo_ops_upgrade.pr._run_gh",
        return_value=make_proc(returncode=1, stderr="no pull request"),
    ):
        exists, url = gh_pr_exists(tmp_path, "repo-ops/deps/x")
    assert exists is False
    assert url is None


def test_gh_pr_exists_false_empty_output(tmp_path: Path, make_proc) -> None:
    with patch("repo_ops_upgrade.pr._run_gh", return_value=make_proc()):
        exists, _url = gh_pr_exists(tmp_path, "repo-ops/deps/x")
    assert exists is False


# ---------------------------------------------------------------------------
# detect_default_branch
# ---------------------------------------------------------------------------


def test_detect_default_branch_from_gh(tmp_path: Path, make_proc) -> None:
    with patch(
        "repo_ops_upgrade.pr._run_gh", return_value=make_proc(stdout="develop\n")
    ):
        assert detect_default_branch(tmp_path) == "develop"


def test_detect_default_branch_fallback_origin_head(tmp_path: Path, make_proc) -> None:
    with (
        patch(
            "repo_ops_upgrade.pr._run_gh",
            return_value=make_proc(returncode=1, stderr="err"),
        ),
        patch(
            "repo_ops_upgrade.pr.run_git",
            return_value=make_proc(stdout="origin/main\n"),
        ),
    ):
        assert detect_default_branch(tmp_path) == "main"


def test_detect_default_branch_ultimate_fallback(tmp_path: Path, make_proc) -> None:
    with (
        patch(
            "repo_ops_upgrade.pr._run_gh",
            return_value=make_proc(returncode=1, stderr="err"),
        ),
        patch(
            "repo_ops_upgrade.pr.run_git",
            return_value=make_proc(returncode=1, stderr="err"),
        ),
    ):
        assert detect_default_branch(tmp_path) == "main"


# ---------------------------------------------------------------------------
# open_or_update_pr
# ---------------------------------------------------------------------------


def test_open_or_update_pr_opens_when_none_exists(tmp_path: Path, make_proc) -> None:
    url = "https://github.com/org/repo/pull/1"

    def _gh_side(cmd, **kw):
        if "view" in cmd:
            return make_proc(returncode=1, stderr="err")  # no existing PR
        return make_proc(stdout=url + "\n")  # pr create succeeds

    with patch("repo_ops_upgrade.pr._run_gh", side_effect=_gh_side):
        status, result = open_or_update_pr(
            tmp_path, "b", base="main", title="T", body="B"
        )
    assert status == "opened"
    assert result == url


def test_open_or_update_pr_noop_when_exists(tmp_path: Path, make_proc) -> None:
    existing_url = "https://github.com/org/repo/pull/7"

    def _gh_side(cmd, **kw):
        if "view" in cmd:
            return make_proc(stdout=existing_url + "\n")
        pytest.fail("gh pr create must not be called when PR exists")

    with patch("repo_ops_upgrade.pr._run_gh", side_effect=_gh_side):
        status, url = open_or_update_pr(tmp_path, "b", base="main", title="T", body="B")
    assert status == "exists"
    assert url == existing_url


def test_open_or_update_pr_returns_failed_on_create_error(
    tmp_path: Path, make_proc
) -> None:
    def _gh_side(cmd, **kw):
        if "view" in cmd:
            return make_proc(returncode=1, stderr="err")
        return make_proc(returncode=1, stderr="gh pr create: authentication required")

    with patch("repo_ops_upgrade.pr._run_gh", side_effect=_gh_side):
        status, err = open_or_update_pr(tmp_path, "b", base="main", title="T", body="B")
    assert status == "failed"
    assert "authentication" in (err or "")


# ---------------------------------------------------------------------------
# run_pr_flow — full integration (all subprocess mocked)
# ---------------------------------------------------------------------------


def _mock_happy_path(make_proc, branch: str = "repo-ops/deps/org-my-repo") -> list:
    """Return a side_effect list for the happy-path run_pr_flow mock sequence.

    Call order inside run_pr_flow:
    1. gh_available → subprocess.run(["gh", "--version"])
    2. create_or_checkout_branch → run_git rev-parse (not exists) + run_git checkout -b
    3. push_branch → run_git push
    4. detect_default_branch → _run_gh gh repo view
    5. open_or_update_pr → _run_gh gh pr view (no PR) + _run_gh gh pr create
    """
    pr_url = "https://github.com/org/my-repo/pull/1"
    return [
        make_proc(stdout="gh version 2.0"),  # gh --version
        make_proc(returncode=1, stderr="err"),  # rev-parse: branch does NOT exist
        make_proc(),  # checkout -b
        make_proc(),  # push --set-upstream
        make_proc(stdout="main\n"),  # gh repo view defaultBranchRef
        make_proc(returncode=1, stderr="err"),  # gh pr view: no open PR
        make_proc(stdout=pr_url + "\n"),  # gh pr create
    ]


def test_run_pr_flow_happy_path(tmp_path: Path, make_proc) -> None:
    """Full happy path: branch created, pushed, PR opened."""
    bundle = _bundle(tmp_path)
    pr_url = "https://github.com/org/my-repo/pull/1"
    side_effects = _mock_happy_path(make_proc)

    _all_calls: list = []

    def _mock_run_git(repo, *args, **kwargs):
        result = side_effects.pop(0)
        _all_calls.append(("git", args))
        return result

    def _mock_run_gh(cmd, **kwargs):
        result = side_effects.pop(0)
        _all_calls.append(("gh", cmd))
        return result

    def _mock_subprocess_run(cmd, **kwargs):
        result = side_effects.pop(0)
        _all_calls.append(("subprocess", cmd))
        return result

    with (
        patch("subprocess.run", side_effect=_mock_subprocess_run),
        patch("repo_ops_upgrade.pr.run_git", side_effect=_mock_run_git),
        patch("repo_ops_upgrade.pr._run_gh", side_effect=_mock_run_gh),
    ):
        outcome = run_pr_flow(tmp_path, bundle)

    assert outcome.status == "opened"
    assert outcome.pr_url == pr_url
    assert outcome.branch == "repo-ops/deps/org-my-repo"


def test_run_pr_flow_idempotent_existing_pr(tmp_path: Path, make_proc) -> None:
    """When a PR already exists, run_pr_flow returns 'exists' without re-opening."""
    bundle = _bundle(tmp_path)
    existing_url = "https://github.com/org/my-repo/pull/5"

    effects = [
        make_proc(stdout="gh version 2"),  # gh --version
        make_proc(returncode=1, stderr="err"),  # rev-parse: branch not local
        make_proc(),  # checkout -b
        make_proc(),  # push
        make_proc(stdout="main\n"),  # detect default branch
        make_proc(stdout=existing_url + "\n"),  # gh pr view: PR exists → no-op
    ]

    with (
        patch("subprocess.run", side_effect=[effects[0]]),
        patch(
            "repo_ops_upgrade.pr.run_git",
            side_effect=[effects[1], effects[2], effects[3]],
        ),
        patch("repo_ops_upgrade.pr._run_gh", side_effect=[effects[4], effects[5]]),
    ):
        outcome = run_pr_flow(tmp_path, bundle)

    assert outcome.status == "exists"
    assert outcome.pr_url == existing_url


def test_run_pr_flow_idempotent_existing_branch(tmp_path: Path, make_proc) -> None:
    """When the branch already exists locally, checkout (not -b) is used."""
    bundle = _bundle(tmp_path)
    pr_url = "https://github.com/org/my-repo/pull/2"
    git_calls: list[tuple] = []

    effects = {
        "subprocess": [make_proc(stdout="gh version 2")],
        "git": [
            make_proc(stdout="abc123"),  # rev-parse: branch EXISTS
            make_proc(),  # checkout (no -b)
            make_proc(),  # push
            make_proc(returncode=1, stderr="err"),  # origin/HEAD: not set
        ],
        "gh": [
            make_proc(stdout="main\n"),  # gh repo view
            make_proc(returncode=1, stderr="err"),  # gh pr view: no PR
            make_proc(stdout=pr_url + "\n"),  # gh pr create
        ],
    }

    def _mock_git(repo, *args, **kwargs):
        result = effects["git"].pop(0)
        git_calls.append(args)
        return result

    with (
        patch("subprocess.run", side_effect=effects["subprocess"]),
        patch("repo_ops_upgrade.pr.run_git", side_effect=_mock_git),
        patch("repo_ops_upgrade.pr._run_gh", side_effect=effects["gh"]),
    ):
        outcome = run_pr_flow(tmp_path, bundle)

    assert outcome.status == "opened"
    # Second git call must be plain checkout, not checkout -b
    assert git_calls[1] == ("checkout", "repo-ops/deps/org-my-repo")


def test_run_pr_flow_gh_missing(tmp_path: Path) -> None:
    """When gh is not installed, returns failed with helpful message."""
    bundle = _bundle(tmp_path)

    with patch("subprocess.run", side_effect=FileNotFoundError):
        outcome = run_pr_flow(tmp_path, bundle)

    assert outcome.status == "failed"
    assert "gh CLI not found" in (outcome.detail or "")
    assert outcome.branch == "repo-ops/deps/org-my-repo"


def test_run_pr_flow_push_failure(tmp_path: Path, make_proc) -> None:
    """Push failure marks the repo failed; batch continues (no exception)."""
    bundle = _bundle(tmp_path)

    effects_sub = [make_proc(stdout="gh version 2")]
    effects_git = [
        make_proc(returncode=1, stderr="err"),  # rev-parse: no local branch
        make_proc(),  # checkout -b
        make_proc(returncode=1, stderr="push rejected"),  # push failure
    ]

    with (
        patch("subprocess.run", side_effect=effects_sub),
        patch("repo_ops_upgrade.pr.run_git", side_effect=effects_git),
        patch("repo_ops_upgrade.pr._run_gh"),
    ):
        outcome = run_pr_flow(tmp_path, bundle)

    assert outcome.status == "failed"
    assert "push" in (outcome.detail or "").lower()


def test_run_pr_flow_pr_create_auth_error(tmp_path: Path, make_proc) -> None:
    """gh pr create auth failure is captured as failed, not raised."""
    bundle = _bundle(tmp_path)

    effects_git = [
        make_proc(returncode=1, stderr="err"),
        make_proc(),
        make_proc(),
        make_proc(returncode=1, stderr="err"),
    ]
    effects_gh = [
        make_proc(stdout="main\n"),  # detect default branch
        make_proc(returncode=1, stderr="err"),  # gh pr view: no PR
        make_proc(returncode=1, stderr="authentication required"),  # gh pr create fails
    ]

    with (
        patch("subprocess.run", return_value=make_proc(stdout="gh version 2")),
        patch("repo_ops_upgrade.pr.run_git", side_effect=effects_git),
        patch("repo_ops_upgrade.pr._run_gh", side_effect=effects_gh),
    ):
        outcome = run_pr_flow(tmp_path, bundle)

    assert outcome.status == "failed"
    assert "authentication" in (outcome.detail or "")


# ---------------------------------------------------------------------------
# Default-off parity: --pr absent means no push / gh calls at all
# ---------------------------------------------------------------------------


def test_default_off_no_pr_calls(tmp_path: Path) -> None:
    """Without --pr, run_upgrade must never call run_pr_flow."""
    from repo_ops_upgrade.run import run_upgrade

    pr_calls: list = []

    def _fake_body(**kwargs):
        return 0

    def _fake_pr_flow(*a, **kw):
        pr_calls.append((a, kw))
        return PROutcome(Path("/fake"), "fake", "opened")

    with (
        patch("repo_ops_upgrade.run._run_upgrade_body", _fake_body),
        patch("repo_ops_upgrade.pr.run_pr_flow", _fake_pr_flow),
    ):
        run_upgrade(apply=True, argv=[str(tmp_path), "--commit", "conventional"])

    assert pr_calls == [], "run_pr_flow must not be called when --pr is absent"


# ---------------------------------------------------------------------------
# CLI flag wiring
# ---------------------------------------------------------------------------


def test_pr_flags_accepted_by_parser() -> None:
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=True)
    args = parser.parse_args(
        ["--pr", "--pr-base", "develop", "--branch-prefix", "bumps"]
    )
    assert args.pr is True
    assert args.pr_base == "develop"
    assert args.branch_prefix == "bumps"


def test_pr_flags_default_off() -> None:
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=True)
    args = parser.parse_args([])
    assert getattr(args, "pr", False) is False
    assert getattr(args, "pr_base", None) is None
    assert getattr(args, "branch_prefix", "repo-ops/deps") == "repo-ops/deps"


def test_pr_flags_absent_from_plan_parser() -> None:
    """--pr is apply-only; plan parser must not expose it."""
    from repo_ops_core.cli import build_upgrade_parser

    parser = build_upgrade_parser(apply=False)
    # parse_args with --pr must error (not a known argument for plan)
    with pytest.raises(SystemExit):
        parser.parse_args(["--pr"])
