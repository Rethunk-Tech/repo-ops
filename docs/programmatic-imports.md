# Programmatic Imports

Python module paths for importing `repo-ops` packages programmatically. Filesystem layout and what each package does: [docs/architecture.md](architecture.md).

Package `__init__.py` files export only `main` (CLI entry) and `register()` (ecosystem plugins). Import the submodule paths below for testable APIs.

**Ecosystem plugins** all follow one shape — `repo_ops_js_{npm,pnpm,yarn,bun}`, `repo_ops_go`, `repo_ops_rust`, `repo_ops_py_{uv,poetry,pip_tools,hatch,pdm,pipenv}`, and `repo_ops_container` each export `register()` and nothing else worth importing. `repo_ops_py.pipenv` additionally re-exports `check_pipenv` / `apply_pipenv`. Remediation hooks follow the same rule under `repo_ops_upgrade_remediations_{core,js,go,py,rust,deno}`.

Everything else:

| Area | Module | Notable exports |
| ------ | -------- | ----------------- |
| CLI | `repo_ops.cli` | subcommand dispatch |
| Config | `repo_ops_core.config` | `load_global_config`, `load_repo_config`, `SETTINGS_TABLE` |
| Shared core | `repo_ops_core` | git, filesystem, process, discovery, CLI helpers, hold rules |
| Models | `repo_ops_models` | `Workspace`, `Change`, … |
| Upgrade entrypoints | `repo_ops_upgrade` | `main_plan`, `main_apply` |
| Upgrade pipeline | `repo_ops_upgrade.{orchestration,registry,discovery,actions,verify,pr}` | plan/apply stages |
| Inventory | `repo_ops_inventory.scan` | dependency footprint |
| Repo status | `repo_ops_status.status` | branch, dirty count, ahead/behind per checkout |
| Push audit | `repo_ops_pushed.audit` | reflog window scan |
| Audit | `repo_ops_audit.audit` | `RemediationItem`, `collect_all_remediations`, `run_audit` |
| Dependabot | `repo_ops_dependabot.dependabot` | `detect_ecosystems`, supersession helpers |
| Doctor | `repo_ops_doctor.doctor` | `probe_all`, `BinarySpec`, `ProbeResult` |
| Lockfiles | `repo_ops_lockfiles` | parsers, snapshot diff |
| SBOM / license | `repo_ops_lockfiles.{sbom,license_policy}` | CycloneDX/SPDX builders, policy scan |
| Node shared | `repo_ops_js` | overrides audit, Deno support |
| Python shared | `repo_ops_py` | PyPI lookup, hold filter |
