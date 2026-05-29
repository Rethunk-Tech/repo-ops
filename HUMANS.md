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
- Optional for upgrades: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, etc.) and `gh` / `actionlint` for GitHub Actions pin audits

## Usage

Console scripts (see `[project.scripts]` in `pyproject.toml`):

| Command | Purpose |
|---------|---------|
| `repos-inventory` | Read-only dependency footprint (direct, versions, on-disk sprawl) |
| `repos-upgrade-plan` | Plan outdated deps, GitHub Actions pin drift, and remediations (no writes) |
| `repos-upgrade-apply` | Apply dependency updates, Actions pin bumps, remediations, and verify gates |
| `repos-pushed` | Commits pushed to remotes in a time window (local reflog) |
| `repo-ops` | Optional dispatcher: `repo-ops inventory`, `plan`, `apply`, `pushed` |

All tools default to scanning [`DEFAULT_ROOTS`](src/repo_ops/core/constants.py) unless you pass explicit root directories.

### Upgrade plan vs apply

- **Plan** (`repos-upgrade-plan`) never mutates the tree. Exit code `1` when actionable drift exists (useful for CI).
- **Apply** (`repos-upgrade-apply`) performs updates. Use `--no-verify` or `--skip-remediations` to narrow scope.

GitHub Actions workflow pins are included in both plan and apply by default. Use `--skip-actions` for dependency-only runs.

Legacy flags (`-y`, `--also-actions`, `--only-actions`) are removed; use `repos-upgrade-apply` instead of `-y` on plan.

Run `--help` on each command for full options.

### Package layout (0.2.1+)

Console scripts are registered in `pyproject.toml` and call domain `main()` functions directly (for example `repo_ops.upgrade.run:main_plan`). The optional `repo-ops` dispatcher lives under `repo_ops.commands.dispatch`.

For programmatic use, prefer these import paths (there is no `repo_ops.upgrade.engine`):

| Area | Module |
|------|--------|
| Upgrade entrypoints | `repo_ops.upgrade` (`run_upgrade`, `main_plan`, `main_apply`) |
| Models | `repo_ops.upgrade.models` |
| Workspace discovery | `repo_ops.upgrade.workspaces` (`discover_all`, `group_by_repo`) |
| Ecosystem check/apply | `repo_ops.upgrade.ecosystems` (`check_workspace`, `apply_workspace`) |
| GitHub Actions pins | `repo_ops.upgrade.actions` |
| Inventory scan | `repo_ops.inventory.scan` |
| Push audit | `repo_ops.pushed.audit` |
| Shared helpers | `repo_ops.core.*` |

## Verification and Testing

After install or changes (full dev verification):

```bash
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src/repo_ops
pytest -q --cov=src/repo_ops --cov-report=term-missing --cov-fail-under=15
python -m build --wheel --sdist
```

The test suite lives under `tests/` and is configured in `pyproject.toml` under `[tool.pytest.ini_options]`. Coverage and mypy configs also live in `pyproject.toml`.

CI runs ruff, mypy, pytest+cov, and build on push/PR to `main` (matrix 3.10–3.12); Dependabot maintains pip and GitHub Actions deps weekly.

## Typical Roots

These utilities scan local checkout trees under standard roots (`/usr/local/src`, `~/Projects`, etc.). They discover repository roots, filter paths, and parse Git/dependency metadata for Rethunk-Tech monorepo workflows.

Pull requests and feedback welcome via the repository issues.
