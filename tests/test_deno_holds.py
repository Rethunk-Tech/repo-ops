from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from repo_ops_core.holds import HoldRule
from repo_ops_js.deno import (
    _deno_import_map_paths,
    _deno_package_matches,
    _enforce_deno_version_ceilings,
    _release_unheld_deno_pin,
    _snapshot_deno_workspace,
    _split_held_deno_changes,
)
from repo_ops_models import Change

_GLOBAL_HOLDS_CACHE = "repo_ops_core.holds._global_holds_cached"
_REPO_HOLDS_CACHE = "repo_ops_core.holds._repo_holds_cached"
_PACKAGE = "npm:held"


def _held_rule(**overrides) -> HoldRule:
    defaults = {"package": _PACKAGE, "max_version": "1.5", "ecosystem": "deno"}
    defaults.update(overrides)
    return HoldRule(**defaults)


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
    with patch(_REPO_HOLDS_CACHE, return_value=()):
        yield


def test_split_held_deno_changes_keeps_target_within_ceiling(tmp_path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change(_PACKAGE, "1.0.0", "1.5.0")
    with patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(),)):
        assert _split_held_deno_changes(ws, [change]) == ([change], [])


def test_snapshot_deno_workspace_includes_maps_and_configs(tmp_path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    import_map = tmp_path / "custom-map.json"
    import_map.write_bytes(b"{}")
    (tmp_path / "deno.json").write_text('{"importMap":"custom-map.json"}', encoding="utf-8")
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


def test_deno_import_map_rejects_parent_and_symlink_escape(tmp_path, make_workspace) -> None:
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
def test_release_unheld_deno_pin_handles_jsonc(tmp_path, filename, make_workspace) -> None:
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


def _enforce(ws, make_proc, *, package=_PACKAGE, registry=None, outdated=None, landed=None):
    with (
        patch(_GLOBAL_HOLDS_CACHE, return_value=(_held_rule(package=package),)),
        patch("repo_ops_js.deno._landed_deno_version", side_effect=landed or ["2.0.0", "1.5.3"]),
        patch("repo_ops_js.deno._deno_lock_package_names", return_value={package}),
        patch("repo_ops_js.deno._deno_lock_versions", return_value=[]),
        patch("repo_ops_js.deno._registry_versions", return_value=registry or []),
        patch("repo_ops_js.deno._deno_outdated_versions", return_value=outdated or []),
        patch("repo_ops_js.deno._snapshot_deno_workspace", return_value=object()),
        patch("repo_ops_js.deno._restore_deno_workspace") as restore_mock,
        patch("repo_ops_js.deno.run", return_value=make_proc()) as run_mock,
    ):
        _enforce_deno_version_ceilings(ws)
    return run_mock, restore_mock


@pytest.mark.parametrize(("package", "bare"), [(_PACKAGE, "held"), ("jsr:@std/held", "@std/held")])
def test_enforce_deno_version_ceilings_clamps_to_the_registry_release(
    tmp_path, package, bare, make_workspace, make_proc
) -> None:
    """deno rejects a ``jsr:``/``npm:`` prefix and a ``<=`` range: the clamp names the bare package at a release."""
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    run_mock, restore_mock = _enforce(
        ws, make_proc, package=package, registry=["1.2.0", "1.5.3", "2.0.0"]
    )

    assert [call.args[0] for call in run_mock.call_args_list] == [
        ["deno", "update", f"{bare}@1.5.3"]
    ]
    restore_mock.assert_not_called()


def test_enforce_deno_version_ceilings_passes_default_import_map(
    tmp_path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    import_map = tmp_path / "import_map.json"
    import_map.write_text("{}", encoding="utf-8")
    run_mock, _ = _enforce(ws, make_proc, registry=["1.5.3"])

    assert run_mock.call_args.args[0] == [
        "deno",
        "update",
        "--import-map",
        str(import_map),
        "held@1.5.3",
    ]


def test_enforce_deno_version_ceilings_raises_when_no_release_fits(
    tmp_path, make_workspace, make_proc
) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    with pytest.raises(RuntimeError, match="failed to clamp"):
        _enforce(ws, make_proc, registry=["2.0.0"], landed=["2.0.0"])


def test_unprefixed_and_prefixed_holds_match_a_prefixed_package(tmp_path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    change = Change("jsr:@std/path", "0.220.1", "1.1.6")
    for configured in ("@std/path", "jsr:@std/path"):
        with patch(
            _GLOBAL_HOLDS_CACHE,
            return_value=(_held_rule(package=configured, max_version="1.0"),),
        ):
            assert _split_held_deno_changes(ws, [change]) == ([], [change])


def test_held_package_stays_in_behind_and_out_of_the_latest_pass(
    tmp_path, make_workspace, make_proc
) -> None:
    from repo_ops_js.deno import apply_deno, check_deno

    ws = make_workspace(tmp_path, ecosystem="deno", manager="deno", label="deno")
    table = "│ jsr:@std/path │ 0.220.1 │ 0.220.1 │ 1.1.6 │\n"
    calls: list[list[str]] = []

    def fake_run(cmd, *_a, **_k):
        calls.append(cmd)
        return make_proc(stdout=table if cmd[:2] == ["deno", "outdated"] else "")

    with (
        patch(
            _GLOBAL_HOLDS_CACHE, return_value=(_held_rule(package="@std/path", max_version="1.0"),)
        ),
        patch("repo_ops_js.deno.shutil.which", return_value="/usr/bin/deno"),
        patch("repo_ops_js.deno.run", side_effect=fake_run),
        patch("repo_ops_js.deno._deno_lock_package_names", return_value=set()),
        patch("repo_ops_js.deno._landed_deno_version", return_value="0.220.1"),
    ):
        changes, behind = check_deno(ws)
        apply_deno(ws)

    assert changes == []
    assert [c.name for c in behind] == ["jsr:@std/path"]
    assert ["deno", "outdated", "--update", "--latest", "!@std/path"] in calls
