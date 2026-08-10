"""Pre-commit wiring for package version-ceiling ("hold") rules."""

from __future__ import annotations

from unittest.mock import patch

from repo_ops_core.holds import HoldRule
from repo_ops_models import Workspace
from repo_ops_upgrade import precommit


def _ws(tmp_path) -> Workspace:
    return Workspace(
        path=tmp_path,
        ecosystem="precommit",
        manager="precommit",
        git_repo=tmp_path,
        label="repo",
    )


def _precommit_config(tmp_path, rev: str = "v1.0.0"):
    config = tmp_path / ".pre-commit-config.yaml"
    config.write_text(
        f"repos:\n- repo: https://github.com/example/hook\n  rev: {rev}\n"
        "  hooks:\n  - id: hook\n",
        encoding="utf-8",
    )
    return config


def _held_rule(**overrides) -> HoldRule:
    defaults = {
        "package": "example/hook",
        "max_version": "1.4",
        "ecosystem": "precommit",
    }
    defaults.update(overrides)
    return HoldRule(**defaults)


def test_check_precommit_filters_tag_rev_above_ceiling(tmp_path) -> None:
    _precommit_config(tmp_path)
    ws = _ws(tmp_path)

    with (
        patch.object(precommit, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(precommit, "_repo_holds_cached", return_value=()),
        patch.object(
            precommit,
            "_fetch_tags_for_repo",
            return_value=["v1.0.0", "v1.5.0"],
        ),
    ):
        assert precommit.check_precommit(ws) == []

def test_check_precommit_filters_sha_refresh_above_ceiling(
    tmp_path, monkeypatch
) -> None:
    _precommit_config(tmp_path, rev="a" * 40)
    ws = _ws(tmp_path)
    refs = [
        ("a" * 40, "v1.0.0"),
        ("b" * 40, "v1.5.0"),
        ("c" * 40, "v2.0.0"),
    ]
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", "1")

    with (
        patch.object(precommit, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(precommit, "_repo_holds_cached", return_value=()),
        patch.object(precommit, "_fetch_tag_refs_for_repo", return_value=refs),
    ):
        assert precommit.check_precommit(ws) == []

def test_apply_precommit_skips_tag_rev_above_ceiling(tmp_path, monkeypatch) -> None:
    config = _precommit_config(tmp_path)
    ws = _ws(tmp_path)
    original = config.read_bytes()
    monkeypatch.delenv("REPO_OPS_PRECOMMIT_UPDATE_LATEST", raising=False)

    with (
        patch.object(precommit, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(precommit, "_repo_holds_cached", return_value=()),
        patch.object(
            precommit,
            "_fetch_tags_for_repo",
            return_value=["v1.0.0", "v1.5.0"],
        ),
    ):
        precommit.apply_precommit(ws)

    assert config.read_bytes() == original

def test_apply_precommit_filters_sha_refresh_above_ceiling(
    tmp_path, monkeypatch
) -> None:
    config = _precommit_config(tmp_path, rev="a" * 40)
    ws = _ws(tmp_path)
    refs = [
        ("a" * 40, "v1.0.0"),
        ("b" * 40, "v1.5.0"),
        ("c" * 40, "v2.0.0"),
    ]
    monkeypatch.setenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", "1")
    original = config.read_bytes()

    with (
        patch.object(precommit, "_global_holds_cached", return_value=(_held_rule(),)),
        patch.object(precommit, "_repo_holds_cached", return_value=()),
        patch.object(precommit, "_fetch_tag_refs_for_repo", return_value=refs),
    ):
        precommit.apply_precommit(ws)

    assert config.read_bytes() == original


def test_expired_precommit_hold_is_ignored_without_rewriting_hold_config(
    tmp_path, monkeypatch
) -> None:
    config = _precommit_config(tmp_path)
    hold_config = tmp_path / ".repo-ops.toml"
    hold_config.write_text(
        '[[hold]]\npackage = "example/hook"\nmax_version = "1.0"\n'
        'until = "2020-01-01"\necosystem = "precommit"\n',
        encoding="utf-8",
    )
    original_hold_config = hold_config.read_bytes()
    ws = _ws(tmp_path)
    monkeypatch.delenv("REPO_OPS_PRECOMMIT_REFRESH_SHA", raising=False)

    with (
        patch.object(precommit, "_global_holds_cached", return_value=()),
        patch.object(
            precommit,
            "_fetch_tags_for_repo",
            return_value=["v1.0.0", "v1.5.0"],
        ),
    ):
        precommit.apply_precommit(ws)

    assert "rev: v1.5.0" in config.read_text(encoding="utf-8")
    assert hold_config.read_bytes() == original_hold_config
