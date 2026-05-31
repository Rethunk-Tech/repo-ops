# Using repo-ops

This document is the authoritative source for installation, environment setup, and usage of the `repo-ops` command-line tools.

## Installation

Requires [uv](https://docs.astral.sh/uv/) and Python >= 3.10.

### Runtime (recommended)

Install the `repo-ops` console command into an isolated tool environment (no project clone required):

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
uv run repo-ops inventory --help
```

`uv.lock` pins dev and build dependencies; use `uv sync --locked` in CI and before pushing.

Editable tool install from your clone (picks up source changes without reinstalling):

```bash
uv tool install -e .
```

### Agent skill installation

After installing the `repo-ops` tool, install the bundled agent skill so Cursor and Claude Code steer LLMs toward `repo-ops` for dependency inventory and upgrade workflows:

```bash
repo-ops install-skill
```

| Target | Install path |
|--------|----------------|
| Cursor (default with `--target both`) | `~/.cursor/skills/repo-ops/` |
| Claude Code | `~/.claude/skills/repo-ops/` |

Options:

- `--target cursor|claude|both` — default `both`
- `--force` — overwrite an existing skill directory
- `--dry-run` — print planned paths without writing

Do not install under `~/.cursor/skills-cursor/` (reserved for Cursor built-ins).

### Verify install

After `uv tool install` (runtime or `-e` development):

```bash
uv tool list          # expect repo-ops 0.3.1+
uv tool dir --bin     # ensure this directory is on PATH
which repo-ops
```

Optional read-only smoke checks on a single repo (replace the path with your checkout):

```bash
repo-ops inventory --flat /path/to/repo-ops
repo-ops plan --skip-actions /path/to/repo-ops
repo-ops pushed --count-only /path/to/repo-ops
```

## Environment

- Python >= 3.10 (`.python-version` pins 3.12 for local/uv default)
- Git available on PATH (tools interact with local repositories)
- Optional for upgrades: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, etc.) and `gh` / `actionlint` for GitHub Actions pin audits

## Usage

The installable console script is **`repo-ops`** (`packages/repo-ops`). Subcommands:

| Subcommand | Purpose |
|------------|---------|
| `repo-ops inventory` | Read-only dependency footprint (direct, versions, on-disk sprawl) |
| `repo-ops plan` | Plan outdated deps, GitHub Actions pin drift, and remediations (no writes) |
| `repo-ops apply` | Apply dependency updates, Actions pin bumps, remediations, and verify gates |
| `repo-ops pushed` | Commits pushed to remotes in a time window (local reflog) |

All tools default to scanning [`DEFAULT_ROOTS`](packages/repo-ops-core/src/repo_ops_core/constants.py) unless you pass explicit root directories.

`--json` emits machine-readable output on every subcommand. For `plan` and `apply`, stdout is a single JSON object only; discovery notes (skipped node dirs, packageManager/lockfile mismatches) are in top-level `skipped` and `warnings` arrays, not mixed into stdout. Human-mode discovery still prints those lines to stderr. `--filter ECO` limits inventory rows or upgrade workspaces to one ecosystem (`node`, `go`, `rust`, `py`).

`--flat` treats each root as a single project (skips the org/proj two-level walk). It is available on `inventory` and upgrade `plan`/`apply`, not on `pushed`.

### Upgrade plan vs apply

- **Plan** (`repo-ops plan`) never mutates the tree. Exit code `1` when actionable drift exists (useful for CI).
- **Apply** (`repo-ops apply`) performs updates. Use `--no-verify` or `--skip-remediations` to narrow scope.

GitHub Actions workflow pins are included in both plan and apply by default. Use `--skip-actions` for dependency-only runs.

**Go modules** — plan and apply share semantics via `REPO_OPS_GO_UPGRADE_MODE` (default `patch`):

| Mode | Plan | Apply |
|------|------|-------|
| `patch` (default) | `go list -m -u -json` filtered to same minor-line patch bumps | `go get -u=patch all`, then `go mod tidy` |
| `explicit` | all `list -u` updates | batched `go get path@version` for each update, then tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u ./...`, `go get -t -u ./...`, tidy |

**Yarn (plan)** — isolated snapshots copy `.yarn/releases/` (Berry) and any `file:../…` sibling checkouts referenced from `package.json`, while still skipping `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in the plan JSON `error` field.

Run `repo-ops <subcommand> --help` for full options.

### Migration from 0.2.x

0.3.0 replaces four standalone console scripts with one **`repo-ops`** entry point and a **uv workspace** under `packages/*`.

| 0.2.x (removed) | 0.3.0 |
|-----------------|-------|
| `repos-inventory` | `repo-ops inventory` |
| `repos-upgrade-plan` | `repo-ops plan` |
| `repos-upgrade-apply` | `repo-ops apply` |
| `repos-pushed` | `repo-ops pushed` |
| `repo_ops.commands.dispatch` | `repo_ops.cli` |
| `repo_ops.upgrade.ecosystems` | `repo_ops_upgrade.registry` (after `repo_ops._register.register_all()`) |

**Operators:** reinstall the global tool so `~/.local/bin/repo-ops` points at the new CLI (`uv tool install -e .` from a clone, or `uv tool upgrade repo-ops` after pulling 0.3.0). Update shell aliases, CI jobs, and automation that invoke `repos-*` or import `repo_ops.upgrade.ecosystems`.

**Org scan (2026-05-30):** `gh search code` in `Rethunk-Tech` found stale `repos-*` / `repo_ops.commands.dispatch` / `repo_ops.upgrade.ecosystems` references only on the default branch of **this** repository (pre-0.3.0 tree: `src/repo_ops/`, root `pyproject.toml` scripts, older README/HUMANS). No hits in other org repos. After merging 0.3.0, re-run:

```bash
gh search code "repos-inventory" --owner Rethunk-Tech
gh search code "repo_ops.upgrade.ecosystems" --owner Rethunk-Tech
```

### Package layout (uv workspace 0.3.1)

The repo is a **uv workspace** under `packages/*`. Ecosystem plugins register checkers/appliers via `repo_ops._register.register_all()` before dispatch. Programmatic import paths:

| Area | Module |
|------|--------|
| CLI dispatcher | `repo_ops.cli` |
| Upgrade entrypoints | `repo_ops_upgrade` (`main_plan`, `main_apply`) |
| Models | `repo_ops_models` |
| Workspace discovery | `repo_ops_upgrade.discovery` (`discover_all`, `group_by_repo`) |
| Ecosystem registry | `repo_ops_upgrade.registry` (`check_workspace`, `apply_workspace`, `register`) |
| GitHub Actions pins | `repo_ops_upgrade.actions` |
| Inventory scan | `repo_ops_inventory.scan` |
| Push audit | `repo_ops_pushed.audit` |
| Lockfile parsers / snapshots | `repo_ops_lockfiles` |
| Node helpers | `repo_ops_js` |
| Shared helpers | `repo_ops_core` (`walk.iter_scanned_dirs`, `parallel.run_bounded_pool`, `discovery.discover_repos`) |

## Verification and Testing

After install or changes (full dev verification):

```bash
uv sync --locked --group dev
uv run python scripts/doc_audit_gate.py --root .
uv run python scripts/check_import_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/repo-ops-core/src packages/repo-ops-models/src packages/repo-ops-lockfiles/src packages/repo-ops-js/src packages/repo-ops-js-npm/src packages/repo-ops-js-pnpm/src packages/repo-ops-js-bun/src packages/repo-ops-js-yarn/src packages/repo-ops-go/src packages/repo-ops-rust/src packages/repo-ops-py/src packages/repo-ops-py-uv/src packages/repo-ops-py-poetry/src packages/repo-ops-py-pip-tools/src packages/repo-ops-py-hatch/src packages/repo-ops-upgrade/src packages/repo-ops-upgrade-remediations-js/src packages/repo-ops-inventory/src packages/repo-ops-pushed/src packages/repo-ops/src
uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=15
uv build packages/repo-ops
```

The test suite lives under `tests/` and is configured in `pyproject.toml` under `[tool.pytest.ini_options]`. Coverage and mypy configs also live in `pyproject.toml`.

CI runs `uv sync --locked`, doc-audit, import-boundary checks, ruff, mypy, pytest+cov, and `uv build packages/repo-ops` on push/PR to `main` (matrix 3.10–3.12). Pushing a version tag (`v*`) runs [`.github/workflows/release.yml`](.github/workflows/release.yml) to build the wheel/sdist and attach them to the GitHub release. Dependabot maintains the uv lockfile and GitHub Actions weekly.

Doc governance uses the Rethunk tier split (README / HUMANS / AGENTS / CONTRIBUTING). Locally:

```bash
uv run python scripts/doc_audit_gate.py --root .
```

## Typical Roots

These utilities scan local checkout trees under standard roots (`/usr/local/src`, `~/Projects`, etc.). They discover repository roots, filter paths, and parse Git/dependency metadata for Rethunk-Tech monorepo workflows.

Pull requests and feedback welcome via the repository issues.
