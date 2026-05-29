# AI Agent Guidelines for repo-ops

This document records conventions and coordination notes for AI coding agents (Cursor, Claude, etc.) and automation working in the repo-ops codebase.

## Project Overview

repo-ops is a lightweight Python package (src layout) that consolidates repository operations and dependency tools for Rethunk-Tech into importable modules with console scripts:

- repos-inventory
- repos-upgrade-plan
- repos-upgrade-apply
- repos-pushed
- repo-ops (optional dispatcher)

It uses hatchling for packaging, pytest for testing, and targets Python >= 3.10. Metadata and entry points live in pyproject.toml.

## Coordination with Other Agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, always run `git status --short` (or equivalent) to identify files currently being modified by others.
- Avoid `src/repo_ops/commands/*` and `tests/*` during active refactor passes unless the change is hygiene-critical and coordinated.
- New untracked modules (e.g. helpers) and tests are frequently added; coordinate to prevent duplicate work or merge conflicts.
- Prefer small, reviewable changes. Batch related edits where safe.

## Layout and Files

- `src/repo_ops/core/` - shared constants, git, filesystem, process, CLI helpers
- `src/repo_ops/inventory/` - dependency counting and `repos-inventory` scan logic
- `src/repo_ops/upgrade/` - plan/apply (`run.py`), `orchestration`, `workspaces`, `actions`, `remediations`, `verify`, `report`, `constants`, `models`, `ecosystems/` (`node`, `yarn`, `go`, `rust`, `python`)
- `src/repo_ops/pushed/` - push reflog audit
- `src/repo_ops/commands/` - optional dispatcher (`dispatch.py`)
- `tests/` - test suite
- `pyproject.toml` - single source of truth for build, scripts, pytest config, and metadata
- `README.md` - user overview
- `LICENSE`, `CONTRIBUTING.md`, `AGENTS.md` - hygiene and process docs
- `.gitignore`, `.editorconfig` - editor/build hygiene

Do not create top-level scripts; everything routes through the package and entry points.

## Development Practices for Agents

- Use `pip install -e .` (or equivalent) for local development; avoid committing venv/ or build/ artifacts.
- Verify changes with `pytest` before finalizing; run `ruff check .` and `ruff format --check .` before committing Python edits.
- When adding CLI commands or features, register them in pyproject.toml and ensure tests cover behavior.
- Keep diffs minimal and preserve existing command output/behavior during refactors.
- Update relevant docs (README, CONTRIBUTING, AGENTS) when conventions or user-facing surfaces change.
- Respect `.editorconfig` (4-space Python, LF, final newlines, etc.).

## Hygiene and Tooling

- Run status checks and keep working tree clean.
- .gitignore covers Python caches, venvs, build artifacts, coverage, IDE/OS noise, and local tool outputs.
- No heavy CI, pre-commit, or lint configs unless explicitly requested; this is a small utility project.
- When in doubt about scope or coordination, ask in the current session rather than guessing.

These guidelines help keep parallel agent work efficient and the repository tidy.

## Incremental Commits for Agents (from now on)

From this point forward, every Task, subagent, or automation working in this repository **must commit incrementally** using MCP-only git operations:

- **Tooling**: Use `batch_commit` and `git_status` from the `plugin-rethunk-git-rethunk-git` (or `user-rethunk-git`) MCP server. Never invoke shell `git commit`, `git add`, or equivalent unless the user has explicitly opted into shell-based git workflows for this session.
- **Batching**: Small, thematic batches (e.g., one doc file or one logical change per commit). Verify with `pytest` (or equivalent) before committing.
- **Messages**: Conventional Commits format. Subject line concise; body explains *why* the change was made (not just what).
- **Pushing**: Always set `push: "never"` (default) unless the user specifically requests a push.
- **Coordination**: Run `git status --short` (shell or MCP) before editing to detect concurrent work. Prefer reviewable history over large monolithic commits.

This ensures safe parallel agent work and aligns with Rethunk good-version-control practices. Commit conventions details live in `CONTRIBUTING.md`.
