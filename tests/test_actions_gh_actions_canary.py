"""Canary holds for unproven Rethunk-Tech/gh-actions tags."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_ops_upgrade import actions as actions_mod
from repo_ops_upgrade.actions import scan_actions

_OLD = "de0fac2e4500dabe0009e67214ff5f5447ce83dd"
_NEW = "11bd71901bbe5b1630ceea73d27597364c9af683"
_CHECKOUT_OLD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_CHECKOUT_NEW = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_GH = "Rethunk-Tech/gh-actions"
_TAG = "v1.31"


def _make_tags_proc(*lines: str) -> MagicMock:
    return MagicMock(returncode=0, stdout="\n".join(lines) + "\n", stderr="")


def _wf(repo: Path, content: str) -> Path:
    wf_dir = repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    wf = wf_dir / "ci.yml"
    wf.write_text(content, encoding="utf-8")
    return wf


def _which(name: str) -> str | None:
    return "/usr/bin/gh" if name == "gh" else None


def _run_for_tags(cmd: list[str], *_args: object, **_kwargs: object) -> MagicMock:
    joined = " ".join(cmd)
    if f"repos/{_GH}/tags" in joined:
        return _make_tags_proc(f"{_TAG} {_NEW}", f"v1.30 {_OLD}", "v1 {_NEW}")
    if "repos/actions/checkout/tags" in joined:
        return _make_tags_proc(
            f"v6.0.3 {_CHECKOUT_NEW}", f"v6.0.2 {_CHECKOUT_OLD}", f"v6 {_CHECKOUT_NEW}"
        )
    return MagicMock(returncode=0, stdout="", stderr="")


@pytest.fixture(autouse=True)
def _isolate_canary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    actions_mod._reset_canary_sweep()


def test_unproven_gh_actions_tag_canaries_first_repo_and_holds_rest(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha_wf = _wf(
        alpha,
        f"jobs:\n  ci:\n    steps:\n      - uses: {_GH}@{_OLD}  # v1.30\n",
    )
    beta_wf = _wf(
        beta,
        f"jobs:\n  ci:\n    steps:\n"
        f"      - uses: {_GH}@{_OLD}  # v1.30\n"
        f"      - uses: actions/checkout@{_CHECKOUT_OLD}  # v6.0.2\n",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=_which),
        patch("repo_ops_upgrade.actions.run", side_effect=_run_for_tags),
        patch("repo_ops_upgrade.actions._github_token_available", return_value=False),
    ):
        scan_actions(alpha, apply=True)
        beta_bumps, _ = scan_actions(beta, apply=True)

    assert _NEW in alpha_wf.read_text(encoding="utf-8")
    beta_text = beta_wf.read_text(encoding="utf-8")
    assert _OLD in beta_text
    assert _NEW not in beta_text
    assert _CHECKOUT_NEW in beta_text
    held = [b for b in beta_bumps if b.detail and "held for canary (alpha)" in b.detail]
    assert [b.latest for b in held] == [_TAG]


def test_proven_gh_actions_tag_bumps_every_repo(tmp_path: Path) -> None:
    state = tmp_path / "xdg-state" / "repo-ops"
    state.mkdir(parents=True)
    (state / "gh-actions-proven.json").write_text(json.dumps({"proven": [_TAG]}), encoding="utf-8")
    alpha_wf = _wf(
        tmp_path / "alpha",
        f"jobs:\n  ci:\n    steps:\n      - uses: {_GH}@{_OLD}  # v1.30\n",
    )
    beta_wf = _wf(
        tmp_path / "beta",
        f"jobs:\n  ci:\n    steps:\n      - uses: {_GH}@{_OLD}  # v1.30\n",
    )

    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=_which),
        patch("repo_ops_upgrade.actions.run", side_effect=_run_for_tags),
        patch("repo_ops_upgrade.actions._github_token_available", return_value=False),
    ):
        scan_actions(tmp_path / "alpha", apply=True)
        scan_actions(tmp_path / "beta", apply=True)

    assert _NEW in alpha_wf.read_text(encoding="utf-8")
    assert _NEW in beta_wf.read_text(encoding="utf-8")


def _gh_run_list(runs: list[dict[str, str]]) -> MagicMock:
    return MagicMock(returncode=0, stdout=json.dumps(runs), stderr="")


@pytest.mark.parametrize(
    ("runs", "green"),
    [
        # CodeQL and Scorecard passed while CI failed on the same SHA.
        (
            [
                {"conclusion": "success", "headSha": _NEW, "workflowName": "CodeQL"},
                {"conclusion": "success", "headSha": _NEW, "workflowName": "Scorecard"},
                {"conclusion": "failure", "headSha": _NEW, "workflowName": "CI"},
            ],
            False,
        ),
        # Only security workflows ran.
        ([{"conclusion": "success", "headSha": _NEW, "workflowName": "CodeQL"}], False),
        # CI passed but another run on the SHA was cancelled.
        (
            [
                {"conclusion": "success", "headSha": _NEW, "workflowName": "CI"},
                {"conclusion": "cancelled", "headSha": _NEW, "workflowName": "Release"},
            ],
            False,
        ),
        ([{"conclusion": "success", "headSha": _NEW, "workflowName": "CI"}], True),
    ],
)
def test_ci_green_needs_a_non_security_success_and_no_failure(
    tmp_path: Path, runs: list[dict[str, str]], green: bool
) -> None:
    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=_which),
        patch("repo_ops_upgrade.actions.run", return_value=_gh_run_list(runs)),
        patch("repo_ops_upgrade.actions._repo_slug", return_value="Rethunk-Tech/x"),
        patch("repo_ops_upgrade.actions._default_branch", return_value="main"),
        patch("repo_ops_upgrade.actions._commit_gh_actions_tags", return_value={_TAG}),
    ):
        assert (_TAG in actions_mod._ci_green_gh_actions_tags(tmp_path)) is green


def test_a_dirty_skipped_repo_is_not_the_canary(
    tmp_path: Path, init_git_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repo_ops_upgrade.run import main_apply

    pinned = f"jobs:\n  ci:\n    steps:\n      - uses: {_GH}@{_OLD}  # v1.30\n"
    wfs = {}
    for name in ("alpha", "beta"):
        repo = tmp_path / "fleet" / name
        init_git_repo(repo)
        wfs[name] = _wf(repo, pinned)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "wf"], check=True)
    (tmp_path / "fleet" / "alpha" / "notes.txt").write_text("operator work\n")
    monkeypatch.setattr("repo_ops_core.cli.default_roots", lambda: [str(tmp_path / "fleet")])

    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=_which),
        patch("repo_ops_upgrade.actions.run", side_effect=_run_for_tags),
        patch("repo_ops_upgrade.actions._github_token_available", return_value=False),
    ):
        main_apply(["--no-commit"])

    assert _OLD in wfs["alpha"].read_text(encoding="utf-8")
    assert _NEW in wfs["beta"].read_text(encoding="utf-8")


def test_green_ci_records_every_tag_pinned_and_unproven_newest_falls_back_to_proven(
    tmp_path: Path,
) -> None:
    state = tmp_path / "xdg-state" / "repo-ops"
    state.mkdir(parents=True)
    (state / "gh-actions-proven.json").write_text(
        json.dumps({"proven": ["v1.30"]}), encoding="utf-8"
    )
    green_repo = tmp_path / "green"
    _wf(green_repo, f"jobs:\n  ci:\n    steps:\n      - uses: {_GH}@{_OLD}  # v1.31\n")
    runs = [{"conclusion": "success", "headSha": _NEW, "workflowName": "CI"}]
    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=_which),
        patch("repo_ops_upgrade.actions.run", return_value=_gh_run_list(runs)),
        patch("repo_ops_upgrade.actions._repo_slug", return_value="Rethunk-Tech/x"),
        patch("repo_ops_upgrade.actions._default_branch", return_value="main"),
        patch(
            "repo_ops_upgrade.actions._commit_gh_actions_tags",
            return_value={"v1.31", "v1.32"},
        ),
    ):
        actions_mod._prove_latest_gh_actions_tag([green_repo])
    assert actions_mod._proven_tags() >= {"v1.30", "v1.31", "v1.32"}


def test_non_canary_repo_advances_to_highest_proven_tag_below_unproven_newest(
    tmp_path: Path,
) -> None:
    state = tmp_path / "xdg-state" / "repo-ops"
    state.mkdir(parents=True)
    (state / "gh-actions-proven.json").write_text(
        json.dumps({"proven": ["v1.30", "v1.31"]}), encoding="utf-8"
    )
    mid = "cccccccccccccccccccccccccccccccccccccccc"
    new = "dddddddddddddddddddddddddddddddddddddddd"

    def run_tags(cmd: list[str], *_a: object, **_k: object) -> MagicMock:
        if f"repos/{_GH}/tags" in " ".join(cmd):
            return _make_tags_proc(f"v1.32 {new}", f"v1.31 {mid}", f"v1.30 {_OLD}")
        return MagicMock(returncode=0, stdout="", stderr="")

    wfs = {
        n: _wf(
            tmp_path / n,
            f"jobs:\n  ci:\n    steps:\n      - uses: {_GH}@{_OLD}  # v1.30\n",
        )
        for n in ("alpha", "beta")
    }
    with (
        patch("repo_ops_upgrade.actions.shutil.which", side_effect=_which),
        patch("repo_ops_upgrade.actions.run", side_effect=run_tags),
        patch("repo_ops_upgrade.actions._github_token_available", return_value=False),
    ):
        scan_actions(tmp_path / "alpha", apply=True)
        scan_actions(tmp_path / "beta", apply=True)
    assert new in wfs["alpha"].read_text(encoding="utf-8")
    assert mid in wfs["beta"].read_text(encoding="utf-8")
