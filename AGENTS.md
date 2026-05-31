# AI Agent Guidelines for repo-ops

This document records conventions and coordination notes for AI coding agents (Cursor, Claude, etc.) and automation working in the repo-ops codebase.

## Project Overview

repo-ops is a **uv workspace** (`packages/*`, version 0.3.2) of Python packages for repository operations and dependency tools for Rethunk-Tech. The installable meta package `repo-ops` exposes a single console script `repo-ops` with subcommands `inventory`, `plan`, `apply`, and `pushed`. It uses the `uv_build` backend, a committed `uv.lock`, pytest for testing, and targets Python >= 3.10.

For command names, install paths, and usage flags, see [HUMANS.md](HUMANS.md).

## Coordination with Other Agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, always run `git status --short` (or equivalent) to identify files currently being modified by others.
- Avoid broad edits across `packages/repo-ops-upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- New workspace packages and tests are frequently added; coordinate to prevent duplicate work or merge conflicts.
- Prefer small, reviewable changes. Batch related edits where safe.

## Layout and Files

- `packages/repo-ops-core/` — shared constants, git, filesystem, process, CLI helpers (`repo_ops_core`)
- `packages/repo-ops-models/` — dataclasses (`repo_ops_models`)
- `packages/repo-ops-lockfiles/` — lockfile parsers and snapshot diff (`repo_ops_lockfiles`)
- `packages/repo-ops-js/` — Node workspace helpers and overrides audit (`repo_ops_js`)
- `packages/repo-ops-js-{npm,pnpm,bun,yarn}/` — PM check/apply plugins with `register()`
- `packages/repo-ops-go/`, `repo-ops-rust/`, `repo-ops-py*/` — ecosystem plugins
- `packages/repo-ops-upgrade/` — plan/apply (`run.py`), `discovery`, `orchestration`, `registry`, `actions`, `verify`, `report`, `post_apply`
- `packages/repo-ops-upgrade-remediations-js/` — JS remediations
- `packages/repo-ops-inventory/`, `repo-ops-pushed/` — inventory and push audit
- `packages/repo-ops/` — meta CLI (`repo_ops.cli`, `repo_ops._register`)
- `tests/` — integration tests; `packages/repo-ops-lockfiles/tests/` — parser unit tests
- Root `pyproject.toml` / `uv.lock` — workspace root (not installable; `package = false`)

Programmatic import paths are listed in [HUMANS.md](HUMANS.md#package-layout-uv-workspace-030). Do not create top-level scripts; everything routes through workspace packages and `repo-ops` entry point.

## Development Practices for Agents

- Install, sync, and verification commands: [HUMANS.md](HUMANS.md) (Installation, Verification and Testing).
- Avoid committing `.venv/`, `dist/`, or other build artifacts (`.venv` is uv-managed and gitignored).
- Run the HUMANS verification suite (or at least `pytest` for code changes) before finalizing commits.
- When adding CLI commands or features, register ecosystem plugins and update `repo_ops._register` / docs.
- Keep diffs minimal and preserve existing command output/behavior during refactors.
- Update tiered docs when user-facing surfaces change: `README.md` for orientation, `HUMANS.md` for install/usage, `CONTRIBUTING.md` for contributor workflow, this file for agent coordination.
- Respect `.editorconfig` (4-space Python, LF, final newlines, etc.).

## Hygiene and Tooling

- Run status checks and keep working tree clean.
- `.gitignore` covers Python caches, venvs, build artifacts, coverage, IDE/OS noise, and local tool outputs.
- When in doubt about scope or coordination, ask in the current session rather than guessing.

These guidelines help keep parallel agent work efficient and the repository tidy.

## Incremental Commits for Agents

Every Task, subagent, or automation working in this repository **must commit incrementally** using MCP-only git operations:

- **Tooling**: Use `batch_commit` and `git_status` from the `plugin-rethunk-git-rethunk-git` (or `user-rethunk-git`) MCP server. Never invoke shell `git commit`, `git add`, or equivalent unless the user has explicitly opted into shell-based git workflows for this session.
- **Batching**: Small, thematic batches (e.g., one doc file or one logical change per commit). Verify before committing (see HUMANS verification).
- **Messages and style**: Follow [CONTRIBUTING.md](CONTRIBUTING.md#commit-conventions) (Conventional Commits, why-focused body).
- **Pushing**: Always set `push: "never"` (default) unless the user specifically requests a push.
- **Coordination**: Run `git status --short` (shell or MCP) before editing to detect concurrent work. Prefer reviewable history over large monolithic commits.

This ensures safe parallel agent work and aligns with Rethunk good-version-control practices.
