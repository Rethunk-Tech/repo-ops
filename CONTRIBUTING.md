# Contributing to repo-ops

Thank you for your interest in repo-ops! This is a small Python package providing local repository operations and dependency maintenance tools for Rethunk-Tech.

## Quick Start for Contributors

1. Clone the repo and create a virtual environment (Python >= 3.10 required):

   ```bash
   git clone https://github.com/Rethunk-Tech/repo-ops.git
   cd repo-ops
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. Run the test suite:

   ```bash
   pytest
   ```

3. The CLI entry points are defined in `pyproject.toml` under `[project.scripts]`.

## Development Notes

- Source layout uses the `src/` package structure.
- Build is managed via `hatchling` (see `pyproject.toml`).
- Add or modify console scripts by updating the `[project.scripts]` table and implementing the `main()` entry in the corresponding module.
- Keep changes focused; this project favors minimal dependencies and simple, maintainable utilities.
- Update `README.md` for user-facing changes and this file or `AGENTS.md` for process/tooling notes.

## Commit Conventions

- Follow [Conventional Commits](https://www.conventionalcommits.org/).
- For agents and automation: use MCP `batch_commit` (see `AGENTS.md` for mandatory policy). Humans may use shell git or MCP.
- Provide a why-focused body explaining the motivation for the change.
- Keep commits small and thematic for easy review.

## Code Style

- 4-space indentation (enforced via `.editorconfig`).
- Follow existing patterns in the codebase for imports, error handling, and Git/subprocess usage.
- Write or update tests for new behavior.

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

Pull requests and issues are welcome!
