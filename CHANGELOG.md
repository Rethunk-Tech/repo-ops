# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Discovery:** Bare default-root `plan`/`apply` now discovers package-manifest workspaces (npm/yarn/pnpm/bun, Go, Python, Rust) at any depth below a root, not just at a fixed `<root>/<org>/<proj>` layout. The non-flat walker previously hardcoded a two-level descent, so checkouts laid out as `/usr/local/src/com.github/<org>/<repo>` (or flat roots like `~/Projects`) silently yielded **zero** workspaces while Actions drift still surfaced — a false sense of completeness. Package discovery now reuses the same depth-agnostic git-repo walk that drives Actions discovery, so both ecosystems cover the same repos. Also surfaces repos nested one level deeper (e.g. `<org>/Bastion/<repo>`) whose dependency drift was previously invisible at every root depth. (#23)

## [0.6.1] - 2026-06-03

### Fixed

- **CLI:** Bare subcommands (e.g. `repo-ops plan` with no path args) no longer fail with `root does not exist: <cwd>/plan`. The dispatcher forwarded `None` instead of the empty remainder list, so the handler's argparse fell back to `sys.argv[1:]` and re-consumed the subcommand token as a positional root. Bare subcommands now correctly scan `DEFAULT_ROOTS`.
- **Types:** Resolved all mypy errors across `inventory`, `upgrade`, and `core` (including a real `_has_actionable_repo_changes` return-type bug that returned a list where `bool` was declared). The CI mypy gate is now green.

## [0.6.0] - 2026-06-03

### Changed

- **GitHub Actions pin policy:** Tag-pinned actions (e.g. `v3.19.0`, `v4`) now normalize to the floating bare-major tag for the *same* major (`v3.19.0` → `v3`, `v4` stays `v4`). Cross-major upgrades (e.g. `v4` → `v6`) are reported but never auto-applied. SHA pins remain report-only. This eliminates per-patch churn on apply consumers and prevents silent breaking major bumps. **Behavior change:** `apply` now writes `v3` instead of `v3.19.0`; consumers that expected exact-patch replacement will see floating major tags instead.

## [0.5.0] - 2026-05-31

### Added

- **Discovery:** `--flat` mode and actions-only repositories (workflow pins without package manifests) participate in plan/apply scans.
- **pip-tools apply:** `requirements.txt` updates via `uv pip compile` when a native pip-tools apply path is unavailable.
- **Release CI:** Tag builds run `pytest` before uploading wheel/sdist artifacts.

### Changed

- **pnpm / yarn:** Default to conservative semver updates (aligned with npm); Yarn isolated plan/apply uses `yarn up` unless `REPO_OPS_YARN_UPDATE_LATEST=1`.
- **Post-apply:** Lockfile snapshot diff detects more change kinds; post-apply commits are scoped on dirty worktrees; checker failures are logged instead of swallowed silently.
- **GitHub Actions:** Actionlint noise reduced; `gh` tag lookups batched; apply skipped when the worktree is dirty.
- **Docs:** Ecosystem upgrade modes, discovery flags, and tier docs no longer pin release versions in agent/human tiers.

### Fixed

- **Security / plan isolation:** Allowlist `packageManager` values; skip Corepack during plan check; Hatch plan checks use a temporary venv; `install-skill` rejects unsafe `--dest` paths; discovery validates cwd and scan roots; default `find` omits `-L` follow symlinks.
- **Parallel apply:** Fail-fast batch exit no longer raises `KeyError` when a sibling repo stops early.
- **CLI:** Reject non-positive `--jobs`.
- **Bun:** Pass `--` before package update targets so flags are not swallowed.
- **Go:** Harden explicit `go get` batches.
- **Inventory:** Skip scan when a default root is missing; prune `SKIP_DIRS` inside `node_modules` dependency counts.
- **pushed:** Resolve pre-push OID from reflog via `rev-parse`.
- **Upgrade apply:** Narrow swallowed exceptions; guard submodule walks; submodule and Actions apply behavior on partially dirty trees.

### Docs

- HUMANS: Go/Bun/pnpm/Yarn upgrade modes, `--flat` / actions-only discovery, and conservative Yarn policy.

## [0.4.0] - 2026-05-31

### Added

- **CLI reliability:** `--log-file` for plan/apply transcripts; `--strict-exit` and verify-failure paths return reliable non-zero exit codes for CI and shell `pipefail`.
- **Plan artifacts:** `--json-out` writes the full plan JSON atomically to a file (stdout contract unchanged).
- **Run summary:** End-of-run roll-up footer with per-repo counts and a capped failure list; JSON reports include matching summary fields ([#14](https://github.com/Rethunk-Tech/repo-ops/issues/14)).
- **Apply policy:** `--on-verify-fail` with `revert` (default keep) to roll back apply changes when verification fails.
- **Submodules:** Discovery, `children-first` ordering via `--submodule-order`, and pointer hints in plan JSON ([#13](https://github.com/Rethunk-Tech/repo-ops/issues/13)).
- **Post-apply commits:** `--commit=conventional|agent` with `--commit-dry-run` and `--commit-on-fail`; pluggable commit-agent providers (default Cursor SDK) behind optional `repo-ops-upgrade[commit-agent-cursor]` ([#15](https://github.com/Rethunk-Tech/repo-ops/issues/15)).
- **GitHub Actions:** Workflow `uses:` pins discovered and updated via ruamel.yaml AST (no substring pin corruption); optional `gh api` resolution for latest action tags; plan/apply output reports workflows scanned.

### Changed

- **Breaking (install only):** Optional Cursor commit-agent extra is named `commit-agent-cursor` (`agent` remains an alias in `pyproject.toml`). Install: `uv pip install 'repo-ops-upgrade[commit-agent-cursor]'`.
- Plan/apply JSON may include new summary, submodule, and Actions-scan fields; consumers should tolerate unknown keys.

### Fixed

- **Bun plan:** Prefer JSON `outdated` output and filter table junk from stdout parsing ([#12](https://github.com/Rethunk-Tech/repo-ops/issues/12)).
- **JS remediations apply:** Report apply outcome text instead of repeating plan copy ([#11](https://github.com/Rethunk-Tech/repo-ops/issues/11)).

### Docs

- HUMANS and bundled skill: bulk-apply playbook, post-apply commit flags, MCP vs headless commit paths, Bastion submodule workflow, `--on-verify-fail`, summary footer, and `--json-out` usage.

## [0.3.5] - 2026-05-31

### Fixed

- **Go mvs apply:** use `go get -u -t all` instead of `go get -u ./...` / `go get -t -u ./...` so transitives and indirects update in one step.

### Changed

- **Docs:** tier dedup (HUMANS install/usage/imports; AGENTS layout/process); install via `packages/repo-ops` and git `#subdirectory=packages/repo-ops`.

## [0.3.4] - 2026-05-31

### Fixed

- **Bun apply:** run `bun update <pkg>@<version>` from `check_bun` results instead of `bun update --latest`, which rewrote lockfile roots to the literal `"latest"` specifier (#8).

## [0.3.3] - 2026-05-31

### Fixed

- **Go plan/apply:** parse `go list -m -u -json` stdout with a streaming JSON decoder so pretty-printed multi-line module objects work (previously treated output as JSONL and failed every Go root).

## [0.3.2] - 2026-05-30

### Fixed

- **GitHub Actions apply:** replace workflow pins only on matched `uses:` lines so shorter tags are not applied as substrings of longer pins (#5).
- **Yarn plan:** isolated snapshots copy `.yarn/releases/` (Berry) and `file:../` sibling checkouts; failed `yarn up` / `install` errors include a stderr tail in plan JSON (#6).
- **Go upgrades:** default `patch` mode aligns plan and apply (`go list -m -u -json` with patch filter; `go get -u=patch all` + tidy). Opt into `explicit` or legacy `mvs` via `REPO_OPS_GO_UPGRADE_MODE` (#7).

### Changed

- HUMANS.md documents Go upgrade modes and Yarn isolated-plan copy semantics.

## [0.3.1] - 2026-05-31

### Added

- Bundled agent skill (`repo_ops/skills/repo-ops/SKILL.md`) and `repo-ops install-skill` to copy it into `~/.cursor/skills/repo-ops/` and `~/.claude/skills/repo-ops/` (`--target`, `--force`, `--dry-run`, `--home`, `--dest`).

### Fixed

- `repo-ops plan` / `apply` with `--json`: discovery SKIP/WARN lines no longer print during the scan; they appear in JSON `skipped` / `warnings` arrays so stdout stays a single JSON document.

## [0.3.0] - 2026-05-30

### Added

- **uv workspace** under `packages/*` (20 internal packages): `repo_ops_core`, `repo_ops_models`, `repo_ops_lockfiles`, `repo_ops_js` (+ npm/pnpm/bun/yarn plugins), `repo_ops_go`, `repo_ops_rust`, `repo_ops_py` (+ uv/poetry/pip-tools/hatch plugins), `repo_ops_upgrade`, `repo_ops_upgrade_remediations_js`, `repo_ops_inventory`, `repo_ops_pushed`, meta `repo_ops`.
- Explicit ecosystem registry (`repo_ops_upgrade.registry`) populated by `repo_ops._register.register_all()` at CLI startup.
- Lockfile parser unit tests in `packages/repo-ops-lockfiles/tests/`.

### Changed

- **Breaking:** Monolith `src/repo_ops` removed. Programmatic imports use `repo_ops_*` package modules (see HUMANS package-layout table).
- **Breaking:** Single console script **`repo-ops`** with subcommands `inventory`, `plan`, `apply`, `pushed` (no flat `repos-*` entry points).
- Lockfile parsing and snapshot/diff consolidated in `repo_ops_lockfiles`; post-apply change detection uses injected checker (no eager `CHECKERS` import hub).
- Shared manifest walks: `repo_ops_core.walk` (`iter_scanned_dirs`, `manifest_kinds`).
- Upgrade parallel execution: `repo_ops_core.parallel.run_bounded_pool`.

### Removed

- **Breaking:** `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, `repos-pushed` console scripts.
- **Breaking:** `repo_ops.upgrade.ecosystems` package and compatibility re-exports.
- `reject_legacy_upgrade_flags` and legacy dep_refresh flag migration hints.

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

[Unreleased]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.5...v0.4.0
[0.3.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.5...v0.3.0
[0.2.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rethunk-Tech/repo-ops/compare/0000000...v0.1.0
