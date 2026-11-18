"""Canary holds for unproven Rethunk-Tech/gh-actions tags."""

from __future__ import annotations

import json
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


def test_unproven_gh_actions_tag_canaries_first_repo_and_holds_rest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
        scan_actions(beta, apply=True)

    assert _NEW in alpha_wf.read_text(encoding="utf-8")
    beta_text = beta_wf.read_text(encoding="utf-8")
    assert _OLD in beta_text
    assert _NEW not in beta_text
    assert _CHECKOUT_NEW in beta_text
    err = capsys.readouterr().err
    assert f"actions: Rethunk-Tech/gh-actions {_TAG} held for canary (alpha)" in err


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
