"""Coverage for pure detect/report drift logic in repo_ops_upgrade_remediations_js.

Exercises _find_biome_configs, _schema_version_from_config, detect_biome/
report_biome, detect_codegen/report_codegen, and _playwright_version_changed
without invoking any apply_* hook (those shell out to real package-manager
tooling and are intentionally out of scope here).
"""

from __future__ import annotations

import json
from pathlib import Path

from repo_ops_models import Change
from repo_ops_upgrade_remediations_js import remediations


def _write_pkg(path: Path, data: dict) -> None:
    (path / "package.json").write_text(json.dumps(data), encoding="utf-8")


def _write_biome_cfg(path: Path, schema_version: str) -> None:
    (path / "biome.json").write_text(
        json.dumps(
            {"$schema": f"https://biomejs.dev/schemas/{schema_version}/schema.json"}
        ),
        encoding="utf-8",
    )


def test_find_biome_configs_root_and_non_glob_member(tmp_path: Path) -> None:
    """Root biome.json plus a non-glob workspace member path are both found."""
    _write_pkg(tmp_path, {"workspaces": ["packages/app"]})
    _write_biome_cfg(tmp_path, "1.5.0")
    member = tmp_path / "packages" / "app"
    member.mkdir(parents=True)
    _write_biome_cfg(member, "1.5.0")

    found = remediations._find_biome_configs(tmp_path)
    names = {str(p.relative_to(tmp_path)) for p in found}
    assert "biome.json" in names
    assert str(Path("packages/app/biome.json")) in names


def test_find_biome_configs_none_present(tmp_path: Path) -> None:
    _write_pkg(tmp_path, {})
    assert remediations._find_biome_configs(tmp_path) == []


def test_schema_version_from_config_present_missing_and_unreadable(
    tmp_path: Path,
) -> None:
    with_schema = tmp_path / "with_schema.json"
    with_schema.write_text(
        json.dumps({"$schema": "https://biomejs.dev/schemas/1.4.2/schema.json"}),
        encoding="utf-8",
    )
    assert remediations._schema_version_from_config(with_schema) == "1.4.2"

    no_schema = tmp_path / "no_schema.json"
    no_schema.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    assert remediations._schema_version_from_config(no_schema) is None

    missing = tmp_path / "does_not_exist.json"
    assert remediations._schema_version_from_config(missing) is None


def test_detect_and_report_biome_schema_drift(tmp_path: Path, make_workspace) -> None:
    """Installed biome version newer than the config's pinned $schema version."""
    _write_pkg(tmp_path, {"devDependencies": {"@biomejs/biome": "1.5.0"}})
    _write_biome_cfg(tmp_path, "1.4.0")

    ctx: dict = {"changes": []}
    assert remediations.detect_biome(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), ctx) is True
    lines = remediations.report_biome(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), ctx)
    assert len(lines) == 1
    assert "biome.json" in lines[0]
    assert "1.4.0" in lines[0] and "1.5.0" in lines[0]


def test_detect_biome_true_when_change_names_biome_even_without_schema_drift(
    tmp_path: Path, make_workspace,
) -> None:
    """ctx containing a Change named after the biome package short-circuits True."""
    _write_pkg(tmp_path, {"devDependencies": {"@biomejs/biome": "1.5.0"}})
    _write_biome_cfg(tmp_path, "1.5.0")  # matches installed — no drift on its own

    ctx = {"changes": [Change("@biomejs/biome", "1.4.0", "1.5.0")]}
    assert remediations.detect_biome(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), ctx) is True


def test_detect_biome_false_when_no_configs_or_no_drift(tmp_path: Path, make_workspace) -> None:
    # No biome.json at all.
    _write_pkg(tmp_path, {"devDependencies": {"@biomejs/biome": "1.5.0"}})
    assert remediations.detect_biome(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), {"changes": []}) is False

    # Config present, matches installed version exactly.
    matched = tmp_path / "matched"
    matched.mkdir()
    _write_pkg(matched, {"devDependencies": {"@biomejs/biome": "1.5.0"}})
    _write_biome_cfg(matched, "1.5.0")
    assert remediations.detect_biome(make_workspace(matched, ecosystem="node", manager="pnpm", label="demo"), {"changes": []}) is False


def test_detect_and_report_codegen_trigger(tmp_path: Path, make_workspace) -> None:
    _write_pkg(tmp_path, {"scripts": {"generate:proto": "buf generate"}})
    ctx = {"changes": [Change("protobufjs", "6.11.0", "7.2.0")]}
    assert remediations.detect_codegen(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), ctx) is True
    lines = remediations.report_codegen(make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo"), ctx)
    assert lines == ["REMEDIATE codegen: would run pnpm run generate:proto"]


def test_detect_codegen_false_without_script_or_without_trigger(tmp_path: Path, make_workspace) -> None:
    # No codegen script present at all.
    no_script = tmp_path / "no_script"
    no_script.mkdir()
    _write_pkg(no_script, {"scripts": {"build": "tsc"}})
    ctx = {"changes": [Change("protobufjs", "6.11.0", "7.2.0")]}
    assert remediations.detect_codegen(make_workspace(no_script, ecosystem="node", manager="pnpm", label="demo"), ctx) is False

    # Script present but no outdated package matches a CODEGEN_TRIGGERS token.
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    _write_pkg(unrelated, {"scripts": {"generate:openapi": "openapi-gen"}})
    ctx2 = {"changes": [Change("react", "18.0.0", "18.2.0")]}
    assert remediations.detect_codegen(make_workspace(unrelated, ecosystem="node", manager="pnpm", label="demo"), ctx2) is False
    # report_codegen lists any present codegen script unconditionally — it does
    # not re-check the trigger; detect_codegen is what gates whether it runs.
    assert remediations.report_codegen(make_workspace(unrelated, ecosystem="node", manager="pnpm", label="demo"), ctx2) == [
        "REMEDIATE codegen: would run pnpm run generate:openapi"
    ]


def test_playwright_version_changed_variants(tmp_path: Path, make_workspace) -> None:
    ws = make_workspace(tmp_path, ecosystem="node", manager="pnpm", label="demo")

    # Matching change with concrete differing versions -> True.
    ctx_diff = {"changes": [Change("@playwright/test", "1.40.0", "1.41.0")]}
    assert remediations._playwright_version_changed(ws, ctx_diff) is True

    # Matching change with identical normalized versions -> False.
    ctx_same = {"changes": [Change("@playwright/test", "1.40.0", "1.40.0")]}
    assert remediations._playwright_version_changed(ws, ctx_same) is False

    # Sentinel from/to values ("?", "changed", "prior"/"updated") -> True default.
    ctx_sentinel = {"changes": [Change("@playwright/test", "?", "updated")]}
    assert remediations._playwright_version_changed(ws, ctx_sentinel) is True

    # No matching change at all -> False.
    ctx_none = {"changes": [Change("react", "18.0.0", "18.2.0")]}
    assert remediations._playwright_version_changed(ws, ctx_none) is False
