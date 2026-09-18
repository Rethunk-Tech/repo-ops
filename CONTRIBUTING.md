# Contributing to repo-ops

Thank you for contributing. Install and usage: [README.md](README.md). Layout, gate and invariants: [AGENTS.md](AGENTS.md).

## Quick start

1. [Develop](README.md#develop): clone and `uv sync --group dev`.
2. Before a PR: [Verification and Testing](docs/testing.md) (at minimum `uv run pytest` for code changes).
3. CLI behavior and flags: [Usage](docs/usage.md).

## Development notes

- Modules live under `src/repo_ops_*`, all shipped in the one `repo-ops` distribution. CLI and registration: `src/repo_ops` (`repo_ops.cli`, `repo_ops._register`).
- CLI flags: `repo-ops <subcommand> --help` (authoritative).

## Git for agents

Commit tooling and git routing are fleet canon — see `~/.claude/CLAUDE.md` § Git & GitHub.

Repo-local only: run [verification](docs/testing.md) before committing.

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
