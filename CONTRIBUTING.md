# Contributing to repo-ops

Thank you for contributing. Orientation: [README.md](README.md). Install, usage, and verification: [HUMANS.md](HUMANS.md). Agent coordination: [AGENTS.md](AGENTS.md).

## Quick start

1. [Installation — Development](docs/installation.md#development): clone, `uv sync --group dev`, optional `uv tool install -e packages/repo-ops`.
2. Before a PR: [Verification and Testing](docs/testing.md) (at minimum `uv run pytest` for code changes).
3. CLI behavior and flags: [Usage](docs/usage.md).

## Development notes

- Workspace members live under `packages/repo-ops-*` (`src/` per package). CLI and registration: `packages/repo-ops` (`repo_ops.cli`, `repo_ops._register`).
- Doc tiers: README (orientation + Documentation table), HUMANS (quickstart + doc links), docs/ (deep reference), AGENTS (layout/coordination/invariants), this file (commits/style/git for agents).

## Git for agents

- Use MCP **`batch_commit`** and **`git_status`** from `plugin-rethunk-git-rethunk-git` (or `user-rethunk-git`). No shell `git commit` / `git add` unless the user opts into shell git for the session.
- **`workspaceRoot`:** absolute path to the repo (for example `/usr/local/src/com.github/Rethunk-Tech/repo-ops`).
- **Batching:** Small, thematic commits. Run [verification](docs/testing.md) before committing.
- **Push:** `push: "never"` unless the user requests a push.

## Commit conventions

- [Conventional Commits](https://www.conventionalcommits.org/).
- Body explains **why**, not a file list.
- Small, thematic commits. Agents follow [Git for agents](#git-for-agents) above; humans may use shell git or MCP.

## Code style

- 4-space indentation (`.editorconfig`).
- PRs pass [verification](docs/testing.md).
- Match existing patterns; add tests for new behavior.

## License

MIT — see [LICENSE](LICENSE).

Pull requests and issues are welcome.
