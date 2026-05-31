# AI Agent Guidelines for repo-ops

Conventions and coordination for AI coding agents (Cursor, Claude Code, etc.) and automation in this repository.

## Project overview

**uv workspace** (`packages/*`): repository operations and dependency tools for Rethunk-Tech. The installable meta package is `packages/repo-ops` (`repo-ops` on PyPI); the repo root is not installable (`package = false`). Console script **`repo-ops`** with subcommands `inventory`, `plan`, `apply`, `pushed`, and `install-skill`.

**Operators:** install, CLI flags, upgrade modes, verification — [HUMANS.md](HUMANS.md). **Contributors:** commits, style, PR checks — [CONTRIBUTING.md](CONTRIBUTING.md).

## Coordination with other agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, run `git status --short` (or MCP `git_status`) to see files others are changing.
- Avoid broad edits across `packages/repo-ops-upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- Prefer small, reviewable changes. Batch related edits where safe.
- Local git commits: MCP `batch_commit` / `git_status` per [CONTRIBUTING.md — Git for agents](CONTRIBUTING.md#git-for-agents). Do not use shell `git commit` unless the user opts into shell git for the session.

## Layout and files

- `packages/repo-ops-core/` — shared constants, git, filesystem, process, CLI helpers (`repo_ops_core`)
- `packages/repo-ops-models/` — dataclasses (`repo_ops_models`)
- `packages/repo-ops-lockfiles/` — lockfile parsers and snapshot diff (`repo_ops_lockfiles`)
- `packages/repo-ops-js/` — Node workspace helpers and overrides audit (`repo_ops_js`)
- `packages/repo-ops-js-{npm,pnpm,bun,yarn}/` — PM check/apply plugins with `register()`
- `packages/repo-ops-go/`, `repo-ops-rust/`, `repo-ops-py*/` — ecosystem plugins
- `packages/repo-ops-upgrade/` — plan/apply (`run.py`), `discovery`, `orchestration`, `registry`, `actions`, `verify`, `report`, `post_apply`
- `packages/repo-ops-upgrade-remediations-js/` — JS remediations
- `packages/repo-ops-inventory/`, `repo-ops-pushed/` — inventory and push audit
- `packages/repo-ops/` — meta CLI (`repo_ops.cli`, `repo_ops._register`, bundled skill under `repo_ops/skills/`)
- `tests/` — integration tests; `packages/repo-ops-lockfiles/tests/` — parser unit tests
- Root `pyproject.toml` / `uv.lock` — workspace manifest only

Ecosystem plugins register via `repo_ops._register.register_all()` before CLI dispatch. Programmatic import paths: [HUMANS.md — Programmatic imports](HUMANS.md#programmatic-imports). Do not add top-level scripts; route through workspace packages and the `repo-ops` entry point.

## Agent invariants (behavior)

Do not duplicate full CLI reference here — [HUMANS.md](HUMANS.md) is canonical. When changing behavior, keep these aligned:

- **Plan vs apply:** plan/check is read-only; Node Corepack runs on apply only (`repo_ops_upgrade.registry.apply_workspace`).
- **Discovery:** `resolve_scan_roots`, optional `--follow-symlinks`, `--flat`, actions-only repos — [HUMANS — Security and discovery](HUMANS.md#security-and-discovery).
- **Docs tiers:** README (orientation + Documentation table), HUMANS (install/usage/verify), this file (layout/coordination), CONTRIBUTING (commits/style).

## Development practices for agents

- Run [HUMANS — Verification and Testing](HUMANS.md#verification-and-testing) before commits (at least `pytest` for code changes).
- Avoid committing `.venv/`, `dist/`, or other build artifacts.
- When adding CLI or plugins, update `repo_ops._register` and the tiered docs above (not duplicate tables into README or AGENTS).
- Respect `.editorconfig` (4-space Python, LF, final newlines).
- When scope is unclear, ask in-session rather than guessing.

## Hygiene and tooling

- `.gitignore` covers Python caches, venvs, build artifacts, coverage, and IDE noise.
