"""Pre-commit wiring for package version-ceiling ("hold") rules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.holds import clear_hold_caches
from repo_ops_upgrade import precommit


@pytest.fixture(autouse=True)
def _isolated_hold_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the global hold config at a tmp dir and drop the process cache.

    ``_global_holds_cached``/``_repo_holds_cached`` are ``@cache``d for the
    process lifetime, so without this every test after the first would keep
    reusing whatever the first one loaded.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    clear_hold_caches()
    yield
    clear_hold_caches()


def _write_global_hold(tmp_path: Path, **overrides) -> None:
    defaults = {
        "package": "example/hook",
        "max_version": "1.4",
        "ecosystem": "precommit",
    }
    defaults.update(overrides)
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir(exist_ok=True)
    lines = ["[[hold]]", *(f'{key} = "{value}"' for key, value in defaults.items())]
    (cfg_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _precommit_config(tmp_path, rev: str = "v1.0.0"):
    config = tmp_path / ".pre-commit-config.yaml"
    config.write_text(
        f"repos:\n- repo: https://github.com/example/hook\n  rev: {rev}\n  hooks:\n  - id: hook\n",
        encoding="utf-8",
    )
    return config


def test_check_precommit_filters_tag_rev_above_ceiling(tmp_path, make_workspace) -> None:
    _precommit_config(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="precommit", manager="precommit", label="repo")
    _write_global_hold(tmp_path)

    with patch.object(
        precommit,
        "_fetch_tags_for_repo",
        return_value=["v1.0.0", "v1.5.0"],
    ):
        assert precommit.check_precommit(ws) == []


def test_check_precommit_surfaces_tag_clamp_at_upstream_latest(tmp_path, make_workspace) -> None:
    _precommit_config(tmp_path, rev="v1.5.0")
    ws = make_workspace(tmp_path, ecosystem="precommit", manager="precommit", label="repo")
    _write_global_hold(tmp_path)

    with patch.object(
        precommit,
        "_fetch_tags_for_repo",
        return_value=["v1.4.0", "v1.5.0"],
    ):
        changes = precommit.check_precommit(ws)

    assert len(changes) == 1
    assert changes[0].from_ver == "v1.5.0"
    assert changes[0].to == "v1.4.0"


def test_check_precommit_filters_sha_refresh_above_ceiling(
    tmp_path, monkeypatch, make_workspace
) -> None:
    _precommit_config(tmp_path, rev="a" * 40)
    ws = make_workspace(tmp_path, ecosystem="precommit", manager="precommit", label="repo")
    refs = [
        ("a" * 40, "v1.0.0"),
        ("b" * 40, "v1.5.0"),
        ("c" * 40, "v2.0.0"),
    ]
    _write_global_hold(tmp_path)

    with patch.object(precommit, "_fetch_tag_refs_for_repo", return_value=refs):
        assert precommit.check_precommit(ws) == []
