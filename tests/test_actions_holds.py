"""GitHub Actions wiring for package version-ceiling ("hold") rules."""

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_ops_core.holds import clear_hold_caches
from repo_ops_upgrade import actions

_ACTION = "actions/checkout"


def _workflow(tmp_path, pin: str):
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "name: CI\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        f"      - uses: {_ACTION}@{pin}\n",
        encoding="utf-8",
    )
    return workflow


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
    defaults = {"package": _ACTION, "max_version": "4.3", "ecosystem": "actions"}
    defaults.update(overrides)
    cfg_dir = tmp_path / "repo-ops"
    cfg_dir.mkdir(exist_ok=True)
    lines = ["[[hold]]", *(f'{key} = "{value}"' for key, value in defaults.items())]
    (cfg_dir / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tag_info(tags: list[str], sha_to_tags: dict[str, list[str]] | None = None) -> dict:
    majors = {
        int(match.group(1)) for tag in tags if (match := re.fullmatch(r"v(\d+)", tag)) is not None
    }
    all_majors = {
        int(match.group(1)) for tag in tags if (match := re.match(r"^v(\d+)", tag)) is not None
    }
    return {
        "bare_majors": majors,
        "max_major": max(all_majors) if all_majors else None,
        "all_tags": set(tags),
        "sha_to_tags": sha_to_tags or {},
    }


def test_check_actions_filters_tag_bump_above_ceiling(tmp_path) -> None:
    _workflow(tmp_path, "v3.0.0")
    info = _tag_info(["v3.0.0", "v3.2.0"])
    _write_global_hold(tmp_path, max_version="3.1")

    with patch.object(actions, "action_tag_info", return_value=info):
        bumps, _ = actions.scan_actions(
            tmp_path,
            False,
            lint_level="off",
        )

    assert bumps
    assert all(bump.latest != "v3.2.0" for bump in bumps)


def test_check_actions_filters_sha_refresh_above_ceiling(tmp_path) -> None:
    current_sha = "a" * 40
    newer_sha = "b" * 40
    _workflow(tmp_path, current_sha)
    info = _tag_info(
        ["v3.0.0", "v3.2.0"],
        {
            current_sha: ["v3.0.0"],
            newer_sha: ["v3.2.0"],
        },
    )
    _write_global_hold(tmp_path, max_version="3.1")

    with (
        patch.dict("os.environ", {"REPO_OPS_ACTIONS_REFRESH_SHA": "1"}),
        patch.object(actions, "action_tag_info", return_value=info),
    ):
        bumps, _ = actions.scan_actions(
            tmp_path,
            False,
            keep_sha=True,
            refresh_sha=True,
            lint_level="off",
        )

    assert all(bump.latest != newer_sha for bump in bumps)


def test_apply_actions_clamps_tag_pin_above_ceiling(tmp_path) -> None:
    workflow = _workflow(tmp_path, "v4.5.0")
    info = _tag_info(["v4.0.0", "v4.3.0", "v4.5.0"])
    _write_global_hold(tmp_path)

    with patch.object(actions, "action_tag_info", return_value=info):
        actions.scan_actions(tmp_path, True, lint_level="off")

    assert "uses: actions/checkout@v4.3.0" in workflow.read_text(encoding="utf-8")


def test_apply_actions_raises_when_no_tag_satisfies_ceiling(tmp_path) -> None:
    _workflow(tmp_path, "v4.5.0")
    info = _tag_info(["v4.5.0", "v4.6.0"])
    _write_global_hold(tmp_path)

    with (
        patch.object(actions, "action_tag_info", return_value=info),
        pytest.raises(
            RuntimeError,
            match=r"hold: failed to clamp actions/checkout back to <=4\.3 \(actions\)",
        ),
    ):
        actions.scan_actions(tmp_path, True, lint_level="off")


def test_expired_actions_hold_is_ignored_without_rewriting_hold_config(
    tmp_path,
) -> None:
    workflow = _workflow(tmp_path, "v4.5.0")
    hold_config = tmp_path / ".repo-ops.toml"
    hold_config.write_text(
        '[[hold]]\npackage = "actions/checkout"\nmax_version = "4.0"\n'
        'until = "2020-01-01"\necosystem = "actions"\n',
        encoding="utf-8",
    )
    original_hold_config = hold_config.read_bytes()
    info = _tag_info(["v4.0.0", "v4.5.0"])

    with patch.object(actions, "action_tag_info", return_value=info):
        actions.scan_actions(tmp_path, True, lint_level="off")

    assert "uses: actions/checkout@v4.5.0" in workflow.read_text(encoding="utf-8")
    assert hold_config.read_bytes() == original_hold_config


def test_check_actions_surfaces_tag_hold_clamp_at_upstream_latest(tmp_path) -> None:
    _workflow(tmp_path, "v4.5.0")
    info = _tag_info(["v4.3.0", "v4.5.0", "v4"])
    _write_global_hold(tmp_path)

    with patch.object(actions, "action_tag_info", return_value=info):
        bumps, _ = actions.scan_actions(
            tmp_path,
            False,
            lint_level="off",
        )

    assert any(bump.latest == "v4.3.0" for bump in bumps)
    assert all(bump.latest != "v4" for bump in bumps)


def test_check_actions_surfaces_sha_hold_clamp_without_refresh(tmp_path) -> None:
    current_sha = "a" * 40
    target_sha = "b" * 40
    _workflow(tmp_path, current_sha)
    info = _tag_info(
        ["v4.3.0", "v4.5.0", "v4"],
        {
            current_sha: ["v4.5.0"],
            target_sha: ["v4.3.0"],
        },
    )
    _write_global_hold(tmp_path)

    with patch.object(actions, "action_tag_info", return_value=info):
        bumps, _ = actions.scan_actions(
            tmp_path,
            False,
            keep_sha=True,
            lint_level="off",
        )

    assert any(bump.latest == target_sha for bump in bumps)
    assert all(bump.detail != "SHA-pinned; report only" for bump in bumps)


def test_apply_actions_clamps_exact_pin_instead_of_normalizing_to_major(
    tmp_path,
) -> None:
    workflow = _workflow(tmp_path, "v4.5.0")
    info = _tag_info(["v4.3.0", "v4.5.0", "v4"])
    _write_global_hold(tmp_path)

    with patch.object(actions, "action_tag_info", return_value=info):
        actions.scan_actions(tmp_path, True, lint_level="off")

    assert "uses: actions/checkout@v4.3.0" in workflow.read_text(encoding="utf-8")
