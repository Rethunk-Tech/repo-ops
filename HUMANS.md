# Using repo-ops

This document is the authoritative source for installation, environment setup, and usage of the `repo-ops` command-line tools.

## Installation

Requires [uv](https://docs.astral.sh/uv/) and Python >= 3.10.

### Runtime (recommended)

Install the five console commands into an isolated tool environment (no project clone required):

```bash
uv tool install git+https://github.com/Rethunk-Tech/repo-ops.git
```

Ensure the tool bin directory is on your `PATH` (see `uv tool dir --bin`; uv documents shell hooks for this).

After a release is published to PyPI:

```bash
uv tool install repo-ops
```

Upgrade an existing tool install:

```bash
uv tool upgrade repo-ops
```

### Development

Clone the repository and sync the locked project environment:

```bash
git clone https://github.com/Rethunk-Tech/repo-ops.git
cd repo-ops
uv sync --group dev
```

Run commands through the project environment without a global install:

```bash
uv run repos-inventory --help
uv run repo-ops inventory --help
```

`uv.lock` pins dev and build dependencies; use `uv sync --locked` in CI and before pushing.

Editable tool install from your clone (picks up source changes without reinstalling):

```bash
uv tool install -e .
```

### Verify install

After `uv tool install` (runtime or `-e` development):

```bash
uv tool list          # expect repo-ops 0.2.5+
uv tool dir --bin     # ensure this directory is on PATH
which repos-inventory
```

Optional read-only smoke checks on a single repo (replace the path with your checkout):

```bash
repos-inventory --flat /path/to/repo-ops
repos-upgrade-plan --skip-actions /path/to/repo-ops
repos-pushed --count-only /path/to/repo-ops
```

The optional `repo-ops` dispatcher mirrors these: `repo-ops inventory`, `plan`, and `pushed` accept the same flags after the subcommand.

## Environment

- Python >= 3.10 (`.python-version` pins 3.12 for local/uv default)
- Git available on PATH (tools interact with local repositories)
- Optional for upgrades: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, etc.) and `gh` / `actionlint` for GitHub Actions pin audits

## Usage

Console scripts are declared in PEP 621 `[project.scripts]` in `pyproject.toml` and installed by `uv tool install` or `uv sync`:

| Command | Purpose |
|---------|---------|
| `repos-inventory` | Read-only dependency footprint (direct, versions, on-disk sprawl) |
| `repos-upgrade-plan` | Plan outdated deps, GitHub Actions pin drift, and remediations (no writes) |
| `repos-upgrade-apply` | Apply dependency updates, Actions pin bumps, remediations, and verify gates |
| `repos-pushed` | Commits pushed to remotes in a time window (local reflog) |
| `repo-ops` | Optional dispatcher: `repo-ops inventory`, `plan`, `apply`, `pushed` |

All tools default to scanning [`DEFAULT_ROOTS`](src/repo_ops/core/constants.py) unless you pass explicit root directories.

`--json` emits machine-readable output on every command (`repos-inventory`, upgrade plan/apply, `repos-pushed`). `--filter ECO` limits inventory rows or upgrade workspaces to one ecosystem (`node`, `go`, `rust`, `py`).

`--flat` treats each root as a single project (skips the org/proj two-level walk). It is available on `repos-inventory` and upgrade plan/apply, not on `repos-pushed`.

### Upgrade plan vs apply

- **Plan** (`repos-upgrade-plan`) never mutates the tree. Exit code `1` when actionable drift exists (useful for CI).
- **Apply** (`repos-upgrade-apply`) performs updates. Use `--no-verify` or `--skip-remediations` to narrow scope.

GitHub Actions workflow pins are included in both plan and apply by default. Use `--skip-actions` for dependency-only runs.

Legacy flags (`-y`, `--also-actions`, `--only-actions`) are removed; use `repos-upgrade-apply` instead of `-y` on plan.

Run `--help` on each command for full options.

### Package layout

`[project.scripts]` maps each command to a domain `main()` (for example `repo_ops.upgrade.run:main_plan`). The optional `repo-ops` dispatcher lives under `repo_ops.commands.dispatch`. Packaging uses the `uv_build` backend; the project is uv-locked (`uv.lock`). Programmatic import paths below are current for **0.2.5** (introduced in 0.2.1).

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
| Shared helpers | `repo_ops.core.*` (`walk.iter_scanned_dirs`, `parallel.run_bounded_pool`, `discovery.discover_repos`) |

## Verification and Testing

After install or changes (full dev verification):

```bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src/repo_ops
uv run pytest -q --cov=src/repo_ops --cov-report=term-missing --cov-fail-under=15
uv build
```

The test suite lives under `tests/` and is configured in `pyproject.toml` under `[tool.pytest.ini_options]`. Coverage and mypy configs also live in `pyproject.toml`.

CI runs `uv sync --locked`, ruff, mypy, pytest+cov, `uv build`, and strict doc-audit on push/PR to `main` (matrix 3.10–3.12); Dependabot maintains the uv lockfile and GitHub Actions weekly.

Doc governance uses the Rethunk tier split (README / HUMANS / AGENTS / CONTRIBUTING). Locally:

```bash
uv run python scripts/doc_audit_gate.py --root .
```

## Typical Roots

These utilities scan local checkout trees under standard roots (`/usr/local/src`, `~/Projects`, etc.). They discover repository roots, filter paths, and parse Git/dependency metadata for Rethunk-Tech monorepo workflows.

Pull requests and feedback welcome via the repository issues.
