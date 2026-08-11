from __future__ import annotations

from unittest.mock import patch

import pytest
from repo_ops_js_bun.bun import (
    _bun_targeted_bump_enabled,
    _bun_update_latest_enabled,
)
from repo_ops_js_pnpm.pnpm import (
    _pnpm_targeted_bump_enabled,
    _pnpm_update_latest_enabled,
)
from repo_ops_js_yarn.yarn import (
    _yarn_targeted_bump_enabled,
    _yarn_update_latest_enabled,
)
from repo_ops_models import Workspace


@pytest.mark.parametrize(
    (
        "manager",
        "module",
        "update_key",
        "targeted_key",
        "update_enabled",
        "targeted_enabled",
    ),
    [
        (
            "yarn",
            "repo_ops_js_yarn.yarn",
            "yarn_update_latest",
            "yarn_targeted_bump",
            _yarn_update_latest_enabled,
            _yarn_targeted_bump_enabled,
        ),
        (
            "bun",
            "repo_ops_js_bun.bun",
            "bun_update_latest",
            "bun_targeted_bump",
            _bun_update_latest_enabled,
            _bun_targeted_bump_enabled,
        ),
        (
            "pnpm",
            "repo_ops_js_pnpm.pnpm",
            "pnpm_update_latest",
            "pnpm_targeted_bump",
            _pnpm_update_latest_enabled,
            _pnpm_targeted_bump_enabled,
        ),
    ],
)
def test_js_flags_resolve_per_checkout_config(
    tmp_path,
    manager,
    module,
    update_key,
    targeted_key,
    update_enabled,
    targeted_enabled,
) -> None:
    latest_ws = Workspace(
        path=tmp_path / "latest",
        ecosystem="node",
        manager=manager,
        git_repo=tmp_path / "latest",
        label=f"{manager}-latest",
    )
    targeted_ws = Workspace(
        path=tmp_path / "targeted",
        ecosystem="node",
        manager=manager,
        git_repo=tmp_path / "targeted",
        label=f"{manager}-targeted",
    )

    def repo_config(repo_root):
        return {
            update_key: repo_root == latest_ws.git_repo,
            targeted_key: repo_root == targeted_ws.git_repo,
        }

    with (
        patch(f"{module}.ambient_env", return_value={}),
        patch(f"{module}.load_global_config", return_value={}),
        patch(f"{module}.load_repo_config", side_effect=repo_config),
    ):
        assert update_enabled(latest_ws) is True
        assert update_enabled(targeted_ws) is False
        assert targeted_enabled(latest_ws) is False
        assert targeted_enabled(targeted_ws) is True
