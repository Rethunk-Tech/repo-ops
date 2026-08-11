"""Tests for releasing unheld Node package pins."""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_js.overrides import release_unheld_pin


def _write_package(tmp_path: Path, data: dict[str, object]) -> Path:
    package_path = tmp_path / "package.json"
    package_path.write_text(json.dumps(data), encoding="utf-8")
    return package_path


def _read_package(package_path: Path) -> dict[str, object]:
    return json.loads(package_path.read_text(encoding="utf-8"))


def test_release_unheld_pin_removes_bare_resolution(tmp_path: Path) -> None:
    package_path = _write_package(
        tmp_path,
        {"resolutions": {"left-pad": "1.3.0"}},
    )

    assert release_unheld_pin(tmp_path, "left-pad") is True

    assert _read_package(package_path) == {}


def test_release_unheld_pin_removes_nested_override_key(tmp_path: Path) -> None:
    package_path = _write_package(
        tmp_path,
        {"overrides": {"foo>left-pad": "1.3.0"}},
    )

    assert release_unheld_pin(tmp_path, "left-pad") is True

    assert _read_package(package_path) == {}


def test_release_unheld_pin_removes_nested_workspace_pin(tmp_path: Path) -> None:
    _write_package(tmp_path, {"workspaces": ["packages/*"]})
    workspace_package = tmp_path / "packages" / "foo" / "package.json"
    workspace_package.parent.mkdir(parents=True)
    workspace_package.write_text(
        json.dumps({"name": "foo", "resolutions": {"left-pad": "1.3.0"}}),
        encoding="utf-8",
    )

    assert release_unheld_pin(tmp_path, "left-pad") is True

    assert _read_package(workspace_package) == {"name": "foo"}


def test_release_unheld_pin_removes_nested_workspace_override(tmp_path: Path) -> None:
    _write_package(tmp_path, {"workspaces": ["packages/*"]})
    workspace_package = tmp_path / "packages" / "foo" / "package.json"
    workspace_package.parent.mkdir(parents=True)
    workspace_package.write_text(
        json.dumps({"name": "foo", "overrides": {"left-pad": "1.3.0"}}),
        encoding="utf-8",
    )

    assert release_unheld_pin(tmp_path, "left-pad") is True

    assert _read_package(workspace_package) == {"name": "foo"}


def test_release_unheld_pin_removes_nested_workspace_pnpm_override(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path, {"workspaces": ["packages/*"]})
    workspace_package = tmp_path / "packages" / "foo" / "package.json"
    workspace_package.parent.mkdir(parents=True)
    workspace_package.write_text(
        json.dumps({"name": "foo", "pnpm": {"overrides": {"left-pad": "1.3.0"}}}),
        encoding="utf-8",
    )

    assert release_unheld_pin(tmp_path, "left-pad") is True

    assert _read_package(workspace_package) == {"name": "foo", "pnpm": {}}


def test_release_unheld_pin_removes_pnpm_override(tmp_path: Path) -> None:
    package_path = _write_package(
        tmp_path,
        {"pnpm": {"overrides": {"left-pad": "1.3.0"}}},
    )

    assert release_unheld_pin(tmp_path, "left-pad") is True

    assert _read_package(package_path) == {"pnpm": {}}


def test_release_unheld_pin_returns_false_when_absent(tmp_path: Path) -> None:
    package_path = _write_package(
        tmp_path,
        {"resolutions": {"is-odd": "3.0.1"}},
    )
    before = package_path.read_bytes()

    assert release_unheld_pin(tmp_path, "left-pad") is False

    assert package_path.read_bytes() == before
