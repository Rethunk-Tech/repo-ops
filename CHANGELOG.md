# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[0.1.0]: https://github.com/Rethunk-Tech/repo-ops/compare/0000000...v0.1.0
