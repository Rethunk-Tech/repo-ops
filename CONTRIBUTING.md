# Contributing to repo-ops

Thank you for your interest in repo-ops! This is a small Python package providing local repository operations and dependency maintenance tools for Rethunk-Tech.

## Quick Start for Contributors

1. Follow [HUMANS.md — Installation (Development)](HUMANS.md#development) to clone the repo and run `uv sync --group dev`.
2. Run verification before opening a PR: [HUMANS.md — Verification and Testing](HUMANS.md#verification-and-testing) (at minimum `uv run pytest` for code changes).
3. CLI entry points and command usage: [HUMANS.md — Usage](HUMANS.md#usage).

## Development Notes

- Source layout uses the `src/` package structure; packaging uses `uv_build` with a committed `uv.lock` (details in HUMANS and `pyproject.toml`).
- Add or modify console scripts by updating `[project.scripts]` and implementing the `main()` entry in the corresponding module.
- Keep changes focused; this project favors minimal runtime dependencies and simple, maintainable utilities.
- Update `README.md` for user-facing orientation; update `HUMANS.md` for install/usage/verify changes; update this file or `AGENTS.md` for contributor or agent process notes.

## Commit Conventions

- Follow [Conventional Commits](https://www.conventionalcommits.org/).
- For agents and automation: use MCP `batch_commit` per [AGENTS.md](AGENTS.md#incremental-commits-for-agents-from-now-on). Humans may use shell git or MCP.
- Provide a why-focused body explaining the motivation for the change.
- Keep commits small and thematic for easy review.

## Code Style

- 4-space indentation (enforced via `.editorconfig`).
- PRs must pass the checks in [HUMANS.md — Verification and Testing](HUMANS.md#verification-and-testing) (`ruff`, `mypy`, `pytest`, etc.).
- Follow existing patterns in the codebase for imports, error handling, and Git/subprocess usage.
- Write or update tests for new behavior.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

Pull requests and issues are welcome!
