from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from repo_ops_core.holds import HoldRule
from repo_ops_js.deno import (
    _deno_import_map_paths,
    _deno_package_matches,
    _deno_targeted_bump_enabled,
    _deno_update_latest_enabled,
    _enforce_deno_version_ceilings,
    _filter_held_deno_changes,
    _release_unheld_deno_pin,
    _snapshot_deno_workspace,
    apply_deno_audit_version_overrides,
    check_deno,
    clear_deno_audit_version_overrides,
    set_deno_audit_version_overrides,
    targeted_bump_behind,
)
from repo_ops_models import Change

_GLOBAL_HOLDS_CACHE = "repo_ops_js.deno._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_js.deno._repo_holds_cached"
_PACKAGE = "npm:held"


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _PACKAGE, "max_version": "1.5", "ecosystem": "deno"}
    defaults.update(overrides)
    return HoldRule(**defaults)


def _seed_is_odd_pin(d) -> None:
    (d / "import_map.json").write_text(
        json.dumps({"imports": {"npm:is-odd": "npm:is-odd@0.1.2"}}),
        encoding="utf-8",
    )


def _seed_assert_pin(d) -> None:
    (d / "import_map.json").write_text(
        json.dumps({"imports": {"jsr:@std/assert": "jsr:@std/assert@1.0.0"}}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("npm:lodash", "npm:lodash", True),
        ("npm:lodash", "lodash", True),
        ("npm:lodash", "jsr:lodash", False),
        ("jsr:@std/assert", "@std/assert", True),
    ],
)
def test_deno_package_matches_registry_aware(left, right, expected) -> None:
    assert _deno_package_matches(left, right) is expected


@pytest.fixture(autouse=True)
def _no_repo_holds():
    clear_deno_audit_version_overrides()
    with patch(_REPO_HOLDS_CACHE, return_value=()):
        yield
    clear_deno_audit_version_overrides()


def test_check_deno_filters_past_ceiling_from_planned_and_behind(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    stdout = "\n".join(
        [
            "Package Current Update Latest",
            f"{_PACKAGE} 1.0.0 2.0.0 2.0.0",
            "npm:behind 1.0.0 1.0.0 2.0.0",
        ]
    )
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", return_value=make_proc(stdout=stdout)),
    ):
        changes, behind = check_deno(ws)

    assert changes == []
    assert behind == [
        Change("npm:behind", "1.0.0", "2.0.0"),
    ]


def test_filter_held_deno_changes_keeps_target_within_ceiling(
    tmp_path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change(_PACKAGE, "1.0.0", "1.5.0")
    with patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)):
        assert _filter_held_deno_changes(ws, [change]) == [change]


def test_targeted_bump_skips_held_target_above_ceiling(
    tmp_path, monkeypatch, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change(_PACKAGE, "1.0.0", "2.0.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch("repo_ops_js.deno.run") as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == []
    assert still_behind == [change]
    run_mock.assert_not_called()


def test_targeted_bump_restores_landing_above_ceiling(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change(_PACKAGE, "1.0.0", "1.4.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "1")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_js.deno._snapshot_deno_workspace",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno._restore_deno_workspace") as restore_mock,
        patch("repo_ops_js.deno._landed_deno_version", return_value="2.0.0"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == []
    assert still_behind == [change]
    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        f"{_PACKAGE}@1.4.0",
    ]
    restore_mock.assert_called_once()


def test_targeted_bump_releases_unheld_import_map_pin(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    _seed_is_odd_pin(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change("npm:is-odd", "0.1.2", "1.0.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=()),
        patch(
            "repo_ops_js.deno._snapshot_deno_workspace",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno._landed_deno_version", return_value="1.0.0"),
        patch("repo_ops_js.deno.assert_targets_resolved"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == [change]
    assert still_behind == []
    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        "--import-map",
        str(tmp_path / "import_map.json"),
        "npm:is-odd",
    ]
    imports = json.loads((tmp_path / "import_map.json").read_text(encoding="utf-8"))[
        "imports"
    ]
    assert imports["npm:is-odd"] == "npm:is-odd"


def test_targeted_bump_keeps_held_import_map_pin(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    _seed_is_odd_pin(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change("npm:is-odd", "0.1.0", "0.1.2")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    with (
        patch(
            _GLOBAL_HOLDS_CACHE,
            return_value=(_held_rule(package="npm:is-odd", max_version="0.1.2"),),
        ),
        patch(
            "repo_ops_js.deno._snapshot_deno_workspace",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno._landed_deno_version", return_value="0.1.2"),
        patch("repo_ops_js.deno.assert_targets_resolved"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == [change]
    assert still_behind == []
    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        "--import-map",
        str(tmp_path / "import_map.json"),
        "npm:is-odd@0.1.2",
    ]
    imports = json.loads((tmp_path / "import_map.json").read_text(encoding="utf-8"))[
        "imports"
    ]
    assert imports["npm:is-odd"] == "npm:is-odd@0.1.2"


def test_targeted_bump_releases_unheld_jsr_import_map_pin(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    _seed_assert_pin(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change("jsr:@std/assert", "1.0.0", "1.1.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=()),
        patch(
            "repo_ops_js.deno._snapshot_deno_workspace",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno._landed_deno_version", return_value="1.1.0"),
        patch("repo_ops_js.deno.assert_targets_resolved"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == [change]
    assert still_behind == []
    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        "--import-map",
        str(tmp_path / "import_map.json"),
        "jsr:@std/assert",
    ]
    imports = json.loads((tmp_path / "import_map.json").read_text(encoding="utf-8"))[
        "imports"
    ]
    assert imports["jsr:@std/assert"] == "jsr:@std/assert"


def test_targeted_bump_keeps_held_jsr_import_map_pin(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    _seed_assert_pin(tmp_path)
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change("jsr:@std/assert", "1.0.0", "1.1.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    with (
        patch(
            _GLOBAL_HOLDS_CACHE,
            return_value=(_held_rule(package="jsr:@std/assert", max_version="1.1.0"),),
        ),
        patch(
            "repo_ops_js.deno._snapshot_deno_workspace",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno._landed_deno_version", return_value="1.1.0"),
        patch("repo_ops_js.deno.assert_targets_resolved"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == [change]
    assert still_behind == []
    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        "--import-map",
        str(tmp_path / "import_map.json"),
        "jsr:@std/assert@1.1.0",
    ]
    imports = json.loads((tmp_path / "import_map.json").read_text(encoding="utf-8"))[
        "imports"
    ]
    assert imports["jsr:@std/assert"] == "jsr:@std/assert@1.0.0"


def test_apply_deno_audit_version_overrides_runs_without_targeted_bump(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    monkeypatch.delenv("REPO_OPS_DENO_TARGETED_BUMP", raising=False)
    set_deno_audit_version_overrides({_PACKAGE: "1.4.0"})
    with (
        patch("repo_ops_js.deno._snapshot_deno_workspace", return_value={}),
        patch("repo_ops_js.deno._landed_deno_version", return_value="1.4.0"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        apply_deno_audit_version_overrides(ws)

    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        f"{_PACKAGE}@1.4.0",
    ]


def test_snapshot_deno_workspace_includes_maps_and_configs(
    tmp_path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    import_map = tmp_path / "custom-map.json"
    import_map.write_bytes(b"{}")
    (tmp_path / "deno.json").write_text(
        '{"importMap":"custom-map.json"}', encoding="utf-8"
    )
    with patch("repo_ops_js.deno.snapshot_workspace_files", return_value={}):
        snapshot = _snapshot_deno_workspace(ws)

    assert import_map in snapshot
    assert tmp_path / "deno.json" in snapshot


def test_deno_import_map_config_precedes_default_map(tmp_path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    configured = tmp_path / "z-map.json"
    configured.write_bytes(b"{}")
    (tmp_path / "import_map.json").write_bytes(b"{}")
    (tmp_path / "deno.json").write_text('{"importMap":"z-map.json"}', encoding="utf-8")

    assert _deno_import_map_paths(ws)[0] == configured


def test_deno_import_map_default_selection_is_stable(tmp_path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    first = tmp_path / "import-map.json"
    second = tmp_path / "import_map.json"
    first.write_bytes(b"{}")
    second.write_bytes(b"{}")

    assert _deno_import_map_paths(ws)[0] == first.resolve()


def test_deno_import_map_rejects_parent_and_symlink_escape(
    tmp_path, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    outside = tmp_path.parent / "outside-import-map.json"
    outside.write_bytes(b"{}")
    (tmp_path / "deno.json").write_text(
        '{"importMap":"../outside-import-map.json"}', encoding="utf-8"
    )
    assert _deno_import_map_paths(ws) == []

    (tmp_path / "deno.json").unlink()
    os.symlink(outside, tmp_path / "import_map.json")
    assert _deno_import_map_paths(ws) == []


@pytest.mark.parametrize("filename", ["deno.jsonc", "import_map.jsonc"])
def test_release_unheld_deno_pin_handles_jsonc(
    tmp_path, filename, make_workspace
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    (tmp_path / filename).write_text(
        """
        {
          // Keep this comment while parsing JSONC.
          "imports": {
            "npm:is-odd": "npm:is-odd@0.1.2",
          },
        }
        """,
        encoding="utf-8",
    )

    assert _release_unheld_deno_pin(ws, "npm:is-odd") is True
    payload = json.loads((tmp_path / filename).read_text(encoding="utf-8"))
    assert payload["imports"]["npm:is-odd"] == "npm:is-odd"


def test_targeted_bump_audit_override_wins_over_hold_ceiling(
    tmp_path, monkeypatch, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change(_PACKAGE, "1.0.0", "2.0.0")
    monkeypatch.setenv("REPO_OPS_DENO_TARGETED_BUMP", "1")
    monkeypatch.setenv("REPO_OPS_DENO_UPDATE_LATEST", "0")
    set_deno_audit_version_overrides({_PACKAGE: "1.4.0"})
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_js.deno._snapshot_deno_workspace",
            side_effect=[object(), object()],
        ),
        patch("repo_ops_js.deno._landed_deno_version", return_value="1.4.0"),
        patch("repo_ops_js.deno.assert_targets_resolved"),
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        applied, still_behind = targeted_bump_behind(ws, [change])

    assert applied == [Change(_PACKAGE, "1.0.0", "1.4.0")]
    assert still_behind == []
    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        f"{_PACKAGE}@1.4.0",
    ]


def test_enforce_deno_version_ceilings_clamps_resolved_above_ceiling(
    tmp_path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_js.deno._landed_deno_version",
            side_effect=["2.0.0", "1.5.0"],
        ),
        patch("repo_ops_js.deno._deno_lock_package_names", return_value={_PACKAGE}),
        patch("repo_ops_js.deno._snapshot_deno_workspace", return_value=object()),
        patch("repo_ops_js.deno._restore_deno_workspace") as restore_mock,
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        _enforce_deno_version_ceilings(ws)

    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        f"{_PACKAGE}@<=1.5",
    ]
    restore_mock.assert_not_called()


def test_enforce_deno_version_ceilings_passes_default_import_map(
    tmp_path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    import_map = tmp_path / "import_map.json"
    import_map.write_text("{}", encoding="utf-8")
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)),
        patch(
            "repo_ops_js.deno._landed_deno_version",
            side_effect=["2.0.0", "1.5.0"],
        ),
        patch("repo_ops_js.deno._deno_lock_package_names", return_value={_PACKAGE}),
        patch("repo_ops_js.deno._snapshot_deno_workspace", return_value=object()),
        patch("repo_ops_js.deno._restore_deno_workspace") as restore_mock,
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        _enforce_deno_version_ceilings(ws)

    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        "--import-map",
        str(import_map),
        f"{_PACKAGE}@<=1.5",
    ]
    restore_mock.assert_not_called()


def test_deno_flags_resolve_per_checkout_config(tmp_path, make_workspace) -> None:
    latest_ws = make_workspace(
        tmp_path / "latest", ecosystem="deno", manager="deno", label="deno"
    )
    targeted_ws = make_workspace(
        tmp_path / "targeted", ecosystem="deno", manager="deno", label="deno"
    )

    def repo_config(repo_root):
        return {
            "deno_update_latest": repo_root == latest_ws.git_repo,
            "deno_targeted_bump": repo_root == targeted_ws.git_repo,
        }

    with (
        patch("repo_ops_js.deno.ambient_env", return_value={}),
        patch("repo_ops_js.deno.load_global_config", return_value={}),
        patch("repo_ops_js.deno.load_repo_config", side_effect=repo_config),
    ):
        assert _deno_update_latest_enabled(latest_ws) is True
        assert _deno_update_latest_enabled(targeted_ws) is False
        assert _deno_targeted_bump_enabled(latest_ws) is False
        assert _deno_targeted_bump_enabled(targeted_ws) is True


@pytest.mark.parametrize("package", [_PACKAGE, "jsr:@std/held"])
def test_enforce_deno_version_ceilings_falls_back_to_published_patch(
    tmp_path, package, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    outdated = json.dumps(
        {
            "packages": [
                {
                    "name": package,
                    "current": "2.0.0",
                    "latest": "1.5.7",
                }
            ]
        }
    )
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(package=package),)),
        patch(
            "repo_ops_js.deno._landed_deno_version",
            side_effect=["2.0.0", "1.5.7"],
        ),
        patch("repo_ops_js.deno._deno_lock_package_names", return_value={package}),
        patch("repo_ops_js.deno._deno_lock_versions", return_value=[]),
        patch("repo_ops_js.deno._snapshot_deno_workspace", return_value=object()),
        patch("repo_ops_js.deno._restore_deno_workspace") as restore_mock,
        patch(
            "repo_ops_js.deno.run",
            side_effect=[
                make_proc(returncode=1, stderr="unsupported range"),
                make_proc(stdout=outdated),
                make_proc(),
            ],
        ) as run_mock,
    ):
        _enforce_deno_version_ceilings(ws)

    assert [call.args[0] for call in run_mock.call_args_list] == [
        ["deno", "update", f"{package}@<=1.5"],
        ["deno", "outdated", "--json"],
        ["deno", "update", f"{package}@1.5.7"],
    ]
    restore_mock.assert_called_once()
