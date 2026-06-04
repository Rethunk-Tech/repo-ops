# AI Agent Guidelines for repo-ops

Conventions and coordination for AI coding agents (Cursor, Claude Code, etc.) and automation in this repository.

## Project overview

**uv workspace** (`packages/*`): repository operations and dependency tools for Rethunk-Tech. The installable meta package is `packages/repo-ops` (`repo-ops` on PyPI); the repo root is not installable (`package = false`). Console script **`repo-ops`** with subcommands `inventory`, `plan`, `apply`, `pushed`, and `install-skill`.

**Operators:** install, CLI flags, upgrade modes, verification — [HUMANS.md](HUMANS.md). **Contributors:** commits, style, PR checks — [CONTRIBUTING.md](CONTRIBUTING.md).

## Coordination with other agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, run MCP `git_status` to see files others are changing.
- Avoid broad edits across `packages/repo-ops-upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- Prefer small, reviewable changes. Batch related edits where safe.
- Local git commits: MCP `batch_commit` / `git_status` per [CONTRIBUTING.md — Git for agents](CONTRIBUTING.md#git-for-agents). Do not use shell `git commit` unless the user opts into shell git for the session.

## Layout and files

Full package map and plugin registration details: [docs/architecture.md](docs/architecture.md).

## Agent invariants (behavior)

Do not duplicate full CLI reference here — [docs/](docs/) is canonical. When changing behavior, keep these aligned:

- **Plan vs apply:** plan/check is read-only; Node Corepack runs on apply only (`repo_ops_upgrade.registry.apply_workspace`).
- **Discovery:** `resolve_scan_roots`, optional `--follow-symlinks`, `--flat`, actions-only repos — [docs/usage.md — Security and discovery](docs/usage.md#security-and-discovery).
- **Docs tiers:** README (orientation + Documentation table), HUMANS (quickstart + doc links), docs/ (deep reference), this file (layout/coordination), CONTRIBUTING (commits/style).

## Development practices for agents

- Run [docs/testing.md — Verification and Testing](docs/testing.md) before commits (at least `pytest` for code changes).
- Avoid committing `.venv/`, `dist/`, or other build artifacts.
- When adding CLI or plugins, update `repo_ops._register` and the tiered docs above (not duplicate tables into README or AGENTS).
- Respect `.editorconfig` (4-space Python, LF, final newlines).
- When scope is unclear, ask in-session rather than guessing.

## Hygiene and tooling

- `.gitignore` covers Python caches, venvs, build artifacts, coverage, and IDE noise.
