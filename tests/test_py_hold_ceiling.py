"""A held uv/pdm package blocked by its manifest bound advances to its hold ceiling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repo_ops_core.holds import HoldRule, clear_hold_caches
from repo_ops_models import Change
from repo_ops_py import uv_lock
from repo_ops_py.direct_requirements import bumped_requirement

_RULE = HoldRule(package="httpx", max_version="0.27", ecosystem="py")
_RELEASES = ["0.25.2", "0.27.0", "0.27.2", "0.28.0", "0.28.1", "0.28.2rc1"]


def _ws(path: Path, make_workspace):
    return make_workspace(path, ecosystem="py", manager="uv", label="demo")


def test_held_package_is_retargeted_to_the_highest_release_within_the_ceiling(
    tmp_path: Path, make_workspace
) -> None:
    clear_hold_caches()
    with (
        patch("repo_ops_core.holds._global_holds_cached", return_value=(_RULE,)),
        patch("repo_ops_py.holds.pypi_releases", return_value=_RELEASES),
        patch.object(uv_lock, "latest_version", return_value="0.28.1"),
        patch.object(uv_lock, "direct_dependencies", return_value={"httpx": "<0.26"}),
        patch.object(uv_lock, "_uv_lock_versions", return_value={"httpx": "0.25.2"}),
    ):
        changes = uv_lock._uv_latest_direct_changes(_ws(tmp_path, make_workspace))
    assert changes == [Change("httpx", "0.25.2", "0.27.2")]


def test_bumped_requirement_under_a_hold_keeps_the_ceiling() -> None:
    assert bumped_requirement("<0.26", "0.27.2", _RULE) == ">=0.27.2,<=0.27.999999"
    assert bumped_requirement("<0.26", "0.28.1") == ">=0.28.1"
