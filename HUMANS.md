# Using repo-ops

This document is the authoritative source for installation, environment setup, and usage of the `repo-ops` command-line tools.

## Installation

Requires Python >= 3.10.

Clone and install in editable mode for development:

```bash
git clone https://github.com/Rethunk-Tech/repo-ops.git
cd repo-ops
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For runtime use, the package can be installed via pip once published (version managed in `pyproject.toml`).

## Environment

- Python >= 3.10
- Git available on PATH (tools interact with local repositories)
- No other runtime dependencies beyond the Python standard library and packaging metadata

## Usage

The following console scripts are registered via `[project.scripts]` in `pyproject.toml`:

- `repo-dep-count` — counts dependencies in repository manifests
- `repo-dep-refresh` — refreshes dependency versions or lockfiles
- `repo-pushed-commits` — reports commits pushed in the last 24 hours

Run with `--help` for each tool's specific options. All tools expect to be invoked from within or pointed at valid git repository roots.

## Verification and Testing

After install or changes:

```bash
pytest -q
```

For development with linting/formatting (Ruff):

```bash
pip install -e ".[dev]"
ruff check .
ruff format .          # or `ruff format --check .` in CI
```

The test suite lives under `tests/` and is configured in `pyproject.toml` under `[tool.pytest.ini_options]`.

CI runs ruff + pytest on push/PR to main/master; Dependabot maintains pip and GitHub Actions deps weekly.

## Typical Roots

These utilities are designed for use inside Rethunk-Tech repositories under `/usr/local/src/com.github/Rethunk-Tech/*`. They discover repository roots, filter paths, and parse Git/dependency metadata while preserving command output compatibility.

Pull requests and feedback welcome via the repository issues.
