# Contributing to repo-ops

Thank you for your interest in repo-ops! This is a small Python package providing local repository operations and dependency maintenance tools for Rethunk-Tech.

## Quick Start for Contributors

1. Install [uv](https://docs.astral.sh/uv/) and clone the repo (Python >= 3.10 required):

   ```bash
   git clone https://github.com/Rethunk-Tech/repo-ops.git
   cd repo-ops
   uv sync --group dev
   ```

2. Run the test suite:

   ```bash
   uv run pytest
   ```

3. CLI entry points are declared in PEP 621 `[project.scripts]` in `pyproject.toml` and run via `uv run <command>`, `uv tool install`, or `uv tool install -e .` from a clone (see `HUMANS.md` for install verification).

## Development Notes

- Source layout uses the `src/` package structure.
- Packaging uses the `uv_build` backend with a committed `uv.lock`.
- Add or modify console scripts by updating `[project.scripts]` and implementing the `main()` entry in the corresponding module.
- Keep changes focused; this project favors minimal runtime dependencies and simple, maintainable utilities.
- Update `README.md` for user-facing changes and this file or `AGENTS.md` for process/tooling notes.

## Commit Conventions

- Follow [Conventional Commits](https://www.conventionalcommits.org/).
- For agents and automation: use MCP `batch_commit` (see `AGENTS.md` for mandatory policy). Humans may use shell git or MCP.
- Provide a why-focused body explaining the motivation for the change.
- Keep commits small and thematic for easy review.

## Code Style

- 4-space indentation (enforced via `.editorconfig`).
- PRs must pass `uv run ruff check .` and `uv run ruff format --check .`.
- Follow existing patterns in the codebase for imports, error handling, and Git/subprocess usage.
- Write or update tests for new behavior.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

Pull requests and issues are welcome!
