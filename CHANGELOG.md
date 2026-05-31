# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.5] - 2026-05-29

### Fixed
- CLI tables: shared column padding so headers align with data (`repos-inventory`, `repos-pushed`).
- Inventory `--flat .`: use resolved directory name when `Path('.').name` is empty (Python 3.12+).

## [0.2.4] - 2026-05-29

### Fixed
- Discovery: find `.git` directories without pruning them away (fixes `repos-pushed` and shared repo discovery).
- Inventory: count Python dependency edges from `uv.lock` and `pyproject.toml` lockfiles.

### Changed
- Documentation tier dedup: README defers install details; AGENTS and CONTRIBUTING each hold a single source per tier.

### Added
- HUMANS: document editable `uv tool` install and verification steps for contributors.
- Tests covering repo discovery and `repos-pushed` regression for `.git` pruning.

## [0.2.3] - 2026-05-29

### Changed
- Migrated packaging from hatchling to `uv_build`; project is uv-locked (`uv.lock`, `.python-version`).
- Dev dependencies moved to `[dependency-groups] dev` (replacing `[project.optional-dependencies] dev`).
- CI and contributor docs use `uv sync`, `uv run`, and `uv build` instead of pip/venv.

### Added
- Canonical install documented via `uv tool install` (git URL today; PyPI when published).
- HUMANS installation section for runtime (`uv tool`) vs development (`uv sync`).

## [0.2.2] - 2026-05-29

### Changed
- JSON upgrade output moved to `upgrade/report.py` (`build_json_payload`, `print_json_report`).
- Lockfile snapshot/diff helpers extracted to `upgrade/ecosystems/node_lockfiles.py`.

### Added
- HUMANS package-layout table for programmatic imports (0.2.1+).
- Subsystem tests (actions, remediations, orchestration, report, inventory `--help`).
- CLI `--help` smoke tests invoke domain `main()` directly (no PATH dependency).

## [0.2.1] - 2026-05-29

### Changed
- Decomposed `upgrade/engine.py` into focused modules: `constants`, `workspaces`, `actions`, `remediations`, `verify`, `report`, `orchestration`, and `ecosystems/` (`node`, `yarn`, `go`, `rust`, `python`).
- Test modules renamed for discoverability: `test_upgrade`, `test_inventory`, `test_pushed`.

### Removed
- Monolithic `upgrade/engine.py` (logic moved to submodules above; import `repo_ops.upgrade.ecosystems` or specific submodules instead).

## [0.2.0] - 2026-05-29

### Added
- Four intent-based console entry points: `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, `repos-pushed`.
- Optional `repo-ops` dispatcher with subcommands `inventory`, `plan`, `apply`, `pushed`.
- Domain package layout: `core/`, `inventory/`, `upgrade/`, `pushed/`.
- PEP 561 `py.typed` marker.

### Changed
- **Breaking:** Removed `repo-dep-count`, `repo-dep-refresh`, and `repo-pushed-commits` script names.
- Split dependency refresh into explicit plan (read-only) and apply commands.
- GitHub Actions pin audit is on by default for upgrade plan/apply; use `--skip-actions` to omit.
- `repos-pushed` uses unified root arguments (`add_roots_argument`).

### Removed
- `-y` / `--yes`, `--also-actions`, and `--only-actions` flags (migration messages point to new commands).

## [0.1.0] - 2026-05-29

### Added
- Initial bootstrap of the `repo-ops` package as a lightweight Python src-layout project using hatchling for packaging.
- Three console entry point CLIs registered in pyproject.toml: `repo-dep-count`, `repo-dep-refresh`, and `repo-pushed-commits`.
- Supporting community and release documentation (README, HUMANS, AGENTS, CONTRIBUTING, SECURITY, LICENSE, and this CHANGELOG).
- GitHub community health files (PR/issue templates, CODEOWNERS, Dependabot, minimal CI for lint/test).
- Agent coordination policies (AGENTS.md) and mandatory MCP-based incremental conventional commits for automation.
- Python >= 3.10 compatibility, Ruff linting/formatting, pytest configuration, and .editorconfig hygiene.

### Changed
- N/A (initial release).

[0.2.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rethunk-Tech/repo-ops/compare/0000000...v0.1.0
