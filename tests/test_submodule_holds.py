"""Submodule package version-ceiling hold tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from repo_ops_core.holds import HoldRule
from repo_ops_upgrade import submodules
from repo_ops_upgrade.submodules import (
    SubmoduleLink,
    apply_submodule_tag_advances,
    check_submodule_tag_advances,
)


def _make_link(tmp_path: Path, *, path: str = "sub") -> SubmoduleLink:
    git_repo = (tmp_path / path).resolve()
    git_repo.mkdir(parents=True, exist_ok=True)
    return SubmoduleLink(
        path=path,
        git_repo=git_repo,
        parent=tmp_path.resolve(),
        url="https://example.com/sub.git",
    )


def _held_rule(**overrides) -> HoldRule:
    defaults = {
        "package": "sub",
        "max_version": "1.4",
        "ecosystem": "submodule",
    }
    defaults.update(overrides)
    return HoldRule(**defaults)


def test_check_surfaces_tag_hold_clamp_at_upstream_latest(tmp_path: Path) -> None:
    link = _make_link(tmp_path)

    with (
        patch.object(submodules, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(submodules, "_repo_holds_cached", return_value=()),
        patch.object(submodules, "_current_submodule_tag", return_value="v1.5.0"),
        patch.object(
            submodules,
            "_fetch_remote_tags",
            return_value=["v1.0.0", "v1.4.0", "v1.5.0"],
        ),
    ):
        bumps = check_submodule_tag_advances([link])

    assert len(bumps) == 1
    assert bumps[0].current_tag == "v1.5.0"
    assert bumps[0].latest_tag == "v1.4.0"
    assert bumps[0].is_actionable


def test_check_surfaces_sha_hold_clamp_without_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPO_OPS_SUBMODULE_REFRESH_SHA", raising=False)
    link = _make_link(tmp_path)
    current_sha = "a" * 40
    refs = [
        (current_sha, "v1.5.0"),
        ("b" * 40, "v1.0.0"),
        ("c" * 40, "v2.0.0"),
    ]

    with (
        patch.object(submodules, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(submodules, "_repo_holds_cached", return_value=()),
        patch.object(submodules, "_current_submodule_tag", return_value=None),
        patch.object(submodules, "_current_submodule_sha", return_value=current_sha),
        patch.object(submodules, "_fetch_remote_tag_refs", return_value=refs),
    ):
        bumps = check_submodule_tag_advances([link])

    assert len(bumps) == 1
    assert bumps[0].current_tag == ""
    assert bumps[0].latest_tag == "v1.0.0"
    assert bumps[0].is_actionable


def test_apply_clamps_tag_above_ceiling(tmp_path: Path, make_proc) -> None:
    link = _make_link(tmp_path)

    with (
        patch.object(submodules, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(submodules, "_repo_holds_cached", return_value=()),
        patch.object(
            submodules,
            "_current_submodule_tag",
            side_effect=["v1.5.0", "v1.4.0"],
        ),
        patch.object(
            submodules,
            "_fetch_remote_tags",
            return_value=["v1.0.0", "v1.4.0", "v1.5.0"],
        ),
        patch.object(submodules, "run_git", return_value=make_proc()) as mock_git,
    ):
        apply_submodule_tag_advances([link], allow_dirty=True)

    assert mock_git.call_count == 3
    assert mock_git.call_args_list[1][0][1:4] == (
        "checkout",
        "--detach",
        "refs/tags/v1.4.0",
    )


def test_apply_raises_when_hold_cannot_be_clamped(tmp_path: Path) -> None:
    link = _make_link(tmp_path)

    with (
        patch.object(submodules, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(submodules, "_repo_holds_cached", return_value=()),
        patch.object(submodules, "_current_submodule_tag", return_value="v1.5.0"),
        patch.object(
            submodules,
            "_fetch_remote_tags",
            return_value=["v1.5.0", "v1.6.0"],
        ),
        pytest.raises(
            RuntimeError,
            match=r"hold: failed to clamp sub back to <=1\.4 \(submodule\)",
        ),
    ):
        apply_submodule_tag_advances([link], allow_dirty=True)


def test_expired_hold_is_ignored_without_config_rewrite(
    tmp_path: Path, make_proc,
) -> None:
    link = _make_link(tmp_path)
    hold_config = tmp_path / ".repo-ops.toml"
    hold_config.write_text(
        '[[hold]]\npackage = "sub"\nmax_version = "1.0"\n'
        'until = "2020-01-01"\necosystem = "submodule"\n',
        encoding="utf-8",
    )
    original_hold_config = hold_config.read_bytes()

    with (
        patch.object(submodules, "_global_holds_cached", return_value=()),
        patch.object(submodules, "_current_submodule_tag", return_value="v1.0.0"),
        patch.object(
            submodules,
            "_fetch_remote_tags",
            return_value=["v1.0.0", "v1.5.0"],
        ),
        patch.object(submodules, "run_git", return_value=make_proc()),
    ):
        bumps = apply_submodule_tag_advances([link], allow_dirty=True)

    assert len(bumps) == 1
    assert bumps[0].latest_tag == "v1.5.0"
    assert hold_config.read_bytes() == original_hold_config
