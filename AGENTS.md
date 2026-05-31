# AI Agent Guidelines for repo-ops

This document records conventions and coordination notes for AI coding agents (Cursor, Claude, etc.) and automation working in the repo-ops codebase.

## Project Overview

repo-ops is a lightweight Python package (src layout) that consolidates repository operations and dependency tools for Rethunk-Tech into importable modules with console scripts. It uses uv with the `uv_build` backend, a committed `uv.lock`, pytest for testing, and targets Python >= 3.10. PEP 621 `[project.scripts]` and metadata live in `pyproject.toml`.

For command names, install paths, and usage flags, see [HUMANS.md](HUMANS.md).

## Coordination with Other Agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, always run `git status --short` (or equivalent) to identify files currently being modified by others.
- Avoid `src/repo_ops/commands/*` and `tests/*` during active refactor passes unless the change is hygiene-critical and coordinated.
- New untracked modules (e.g. helpers) and tests are frequently added; coordinate to prevent duplicate work or merge conflicts.
- Prefer small, reviewable changes. Batch related edits where safe.

## Layout and Files

- `src/repo_ops/core/` - shared constants, git, filesystem, process, CLI helpers
- `src/repo_ops/inventory/` - dependency counting and `repos-inventory` scan logic
- `src/repo_ops/upgrade/` - plan/apply (`run.py`), `orchestration` (`process_workspace`), `workspaces` (`discover_all`, `group_by_repo`), `actions`, `remediations`, `verify`, `report`, `constants`, `models`, `ecosystems/` (`node`, `node_lockfiles`, `yarn`, `go`, `rust`, `python`)
- `src/repo_ops/pushed/` - push reflog audit
- `src/repo_ops/commands/` - optional dispatcher only (`dispatch.py`); **`pyproject.toml` `[project.scripts]` points at domain `module:main`**, not `commands/*` shims
- `tests/` - test suite
- `pyproject.toml` / `uv.lock` - single source of truth for build, scripts, locked deps, pytest config, and metadata
- `README.md` - user overview
- `LICENSE`, `CONTRIBUTING.md`, `AGENTS.md` - hygiene and process docs
- `CLAUDE.md` - convention entrypoint; pointer to this file (not a symlink—edit here only)
- `.gitignore`, `.editorconfig` - editor/build hygiene

Programmatic import paths are listed in [HUMANS.md](HUMANS.md#package-layout). Do not create top-level scripts; everything routes through the package and entry points.

## Development Practices for Agents

- Install, sync, and verification commands: [HUMANS.md](HUMANS.md) (Installation, Verification and Testing).
- Avoid committing `.venv/`, `dist/`, or other build artifacts (`.venv` is uv-managed and gitignored).
- Run the HUMANS verification suite (or at least `pytest` for code changes) before finalizing commits.
- When adding CLI commands or features, register them in `pyproject.toml` and ensure tests cover behavior.
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
