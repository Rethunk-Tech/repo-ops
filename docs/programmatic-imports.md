# Programmatic Imports

Python module paths for importing `repo-ops` packages programmatically.

Package `__init__.py` files typically export only `main` (CLI entry) and `register()` (ecosystem plugins). Import submodule paths below for testable APIs and library reuse.

| Area | Module | Notes |
|------|--------|-------|
| CLI | `repo_ops.cli` | Dispatches subcommands |
| Config | `repo_ops_core.config` | `load_global_config`, `load_repo_config`, `SETTINGS_TABLE` |
| Upgrade entrypoints | `repo_ops_upgrade` | `main_plan`, `main_apply` |
| Upgrade orchestration | `repo_ops_upgrade.orchestration`, `repo_ops_upgrade.registry`, `repo_ops_upgrade.discovery`, `repo_ops_upgrade.actions`, `repo_ops_upgrade.verify`, `repo_ops_upgrade.pr` | Plan/apply pipeline |
| Models | `repo_ops_models` | Shared dataclasses (`Workspace`, `Change`, …) |
| Inventory | `repo_ops_inventory.scan` | Dependency footprint |
| Push audit | `repo_ops_pushed.audit` | Reflog window scan |
| Audit | `repo_ops_audit.audit` | `RemediationItem`, `collect_all_remediations`, `run_audit`, `main` |
| Dependabot | `repo_ops_dependabot.dependabot` | `detect_ecosystems`, supersession helpers, `main` |
| Doctor | `repo_ops_doctor.doctor` | `probe_all`, `BinarySpec`, `ProbeResult`, `main` |
| Lockfiles | `repo_ops_lockfiles` | Parsers, snapshot diff |
| SBOM | `repo_ops_lockfiles.sbom` | `main`, CycloneDX/SPDX builders |
| License | `repo_ops_lockfiles.license_policy` | `main`, policy scan helpers |
| Node shared | `repo_ops_js` | Overrides audit, shared Node helpers |
| npm plugin | `repo_ops_js_npm` | `register()` |
| pnpm plugin | `repo_ops_js_pnpm` | `register()` |
| yarn plugin | `repo_ops_js_yarn` | `register()` |
| bun plugin | `repo_ops_js_bun` | `register()` |
| Go plugin | `repo_ops_go` | `register()` |
| Rust plugin | `repo_ops_rust` | `register()` |
| Python shared | `repo_ops_py` | Shared Python helpers |
| uv plugin | `repo_ops_py_uv` | `register()` |
| poetry plugin | `repo_ops_py_poetry` | `register()` |
| pip-tools plugin | `repo_ops_py_pip_tools` | `register()` |
| hatch plugin | `repo_ops_py_hatch` | `register()` |
| pdm plugin | `repo_ops_py_pdm` | `register()` |
| pipenv plugin | `repo_ops_py_pipenv` | `register()`; `repo_ops_py.pipenv` re-exports `check_pipenv` / `apply_pipenv` |
| Container | `repo_ops_container` | `register()`, Dockerfile/compose pin check/apply |
| Remediations core | `repo_ops_upgrade_remediations_core` | Hook registry and dispatch |
| Remediations JS | `repo_ops_upgrade_remediations_js` | Node post-upgrade hooks |
| Remediations Go | `repo_ops_upgrade_remediations_go` | Go fmt / generate hooks |
| Remediations Py | `repo_ops_upgrade_remediations_py` | Python formatter hooks |
| Remediations Rust | `repo_ops_upgrade_remediations_rust` | Rust fmt hooks |
| Remediations Deno | `repo_ops_upgrade_remediations_deno` | Deno fmt hooks |
| Shared core | `repo_ops_core` | git, filesystem, process, discovery, CLI helpers |

Filesystem layout of `packages/*`: [docs/architecture.md](architecture.md).
