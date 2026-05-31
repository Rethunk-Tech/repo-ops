# AI Agent Guidelines for repo-ops

Conventions and coordination for AI coding agents (Cursor, Claude Code, etc.) and automation in this repository.

## Project overview

**uv workspace** (`packages/*`, version 0.4.0): repository operations and dependency tools for Rethunk-Tech. The installable meta package is `packages/repo-ops` (`repo-ops` on PyPI); the repo root is not installable (`package = false`). Console script **`repo-ops`** with subcommands `inventory`, `plan`, `apply`, `pushed`, and `install-skill`.

Install, usage flags, and verification: [HUMANS.md](HUMANS.md). Commit conventions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Coordination with other agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, run `git status --short` (or MCP `git_status`) to see files others are changing.
- Avoid broad edits across `packages/repo-ops-upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- Prefer small, reviewable changes. Batch related edits where safe.

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

## Development practices for agents

- Sync, tool install, and verification: [HUMANS.md — Installation](HUMANS.md#installation), [Verification and Testing](HUMANS.md#verification-and-testing). Editable global CLI: `uv tool install -e packages/repo-ops`.
- Avoid committing `.venv/`, `dist/`, or other build artifacts.
- Run verification (at least `pytest` for code changes) before commits.
- When adding CLI or plugins, update `repo_ops._register` and tiered docs: `README.md` (orientation), `HUMANS.md` (install/usage), `CONTRIBUTING.md` / this file (process).
- Respect `.editorconfig` (4-space Python, LF, final newlines).

## Hygiene and tooling

- `.gitignore` covers Python caches, venvs, build artifacts, coverage, and IDE noise.
- When scope is unclear, ask in-session rather than guessing.

## Incremental commits for agents

Use MCP-only git for staging and commits:

- **Tooling:** `batch_commit`, `git_status` from `plugin-rethunk-git-rethunk-git` (or `user-rethunk-git`). No shell `git commit` / `git add` unless the user opts into shell git for the session.
- **Batching:** Small, thematic commits. Verify before committing (HUMANS verification suite).
- **Messages:** [CONTRIBUTING.md — Commit conventions](CONTRIBUTING.md#commit-conventions) (Conventional Commits, why-focused body).
- **Pushing:** `push: "never"` unless the user requests a push.
