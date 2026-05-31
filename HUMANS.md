# Using repo-ops

Authoritative install, environment, usage, programmatic imports, and verification for the `repo-ops` CLI.

The **installable package** is `packages/repo-ops`. The repository root is a uv workspace manifest only (`package = false`); `uv tool install` against the clone root fails — use `packages/repo-ops` or the git URL with `#subdirectory=packages/repo-ops` below.

## Installation

Requires [uv](https://docs.astral.sh/uv/) and Python >= 3.10.

### Runtime

Install into an isolated tool environment (no clone):

```bash
uv tool install "git+https://github.com/Rethunk-Tech/repo-ops.git#subdirectory=packages/repo-ops"
```

Put the tool bin directory on `PATH` (`uv tool dir --bin`; see uv shell hooks).

After PyPI publish:

```bash
uv tool install repo-ops
uv tool upgrade repo-ops   # refresh an existing install
```

### Development

```bash
git clone https://github.com/Rethunk-Tech/repo-ops.git
cd repo-ops
uv sync --group dev
```

Run without a global tool install:

```bash
uv run repo-ops inventory --help
```

Use `uv sync --locked` in CI and before pushing.

Editable global CLI from your clone:

```bash
uv tool install -e packages/repo-ops
```

After pulling changes, refresh the tool and bundled agent skill:

```bash
uv sync --locked --group dev
uv tool install -e packages/repo-ops
repo-ops install-skill --force
```

### Agent skill

After the `repo-ops` tool is on `PATH`:

```bash
repo-ops install-skill
```

| Target | Path |
|--------|------|
| Cursor (`--target both` default) | `~/.cursor/skills/repo-ops/` |
| Claude Code | `~/.claude/skills/repo-ops/` |

Options: `--target cursor|claude|both`, `--force`, `--dry-run`. Do not install under `~/.cursor/skills-cursor/` (Cursor built-ins).

### Verify install

```bash
uv tool list          # expect repo-ops 0.3.5+
uv tool dir --bin
which repo-ops
```

Optional smoke checks (replace the path):

```bash
repo-ops inventory --flat /path/to/checkout
repo-ops plan --skip-actions /path/to/checkout
repo-ops pushed --count-only /path/to/checkout
```

## Environment

- Python >= 3.10 (`.python-version` pins 3.12 for local/uv default)
- Git on `PATH`
- Optional for upgrades: ecosystem CLIs (`npm`, `yarn`, `pnpm`, `bun`, `go`, `cargo`, `uv`, …) and `gh` / `actionlint` for Actions pin audits

## Usage

Subcommands (entry point in `packages/repo-ops`):

| Subcommand | Purpose |
|------------|---------|
| `inventory` | Read-only dependency footprint |
| `plan` | Outdated deps, Actions pin drift, remediations (no writes) |
| `apply` | Apply updates, Actions bumps, remediations, verify gates |
| `pushed` | Commits pushed to remotes in a time window (reflog) |
| `install-skill` | Copy bundled agent skill into Cursor/Claude skill dirs |

Default scan roots: [`DEFAULT_ROOTS`](packages/repo-ops-core/src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. `--flat` skips the org/proj two-level walk on `inventory` and upgrade `plan`/`apply` (not `pushed`).

`--json` on every subcommand. For `plan`/`apply`, stdout is one JSON object; discovery notes live in `skipped` / `warnings`. Human mode prints discovery lines to stderr. `--filter ECO` limits to `node`, `go`, `rust`, or `py`.

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly).
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope.
- Actions pins included by default; `--skip-actions` for dependency-only runs.

### Go modules

`REPO_OPS_GO_UPGRADE_MODE` (default `patch`) aligns plan and apply:

| Mode | Plan | Apply |
|------|------|-------|
| `patch` (default) | `go list -m -u -json`, same minor-line patches only | `go get -u=patch all`, `go mod tidy` |
| `explicit` | all `list -u` updates | batched `go get path@version`, tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u -t all`, tidy |

### Yarn (plan)

Isolated snapshots copy `.yarn/releases/` (Berry) and `file:../…` siblings from `package.json`; skip `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in plan JSON `error`.

Run `repo-ops <subcommand> --help` for all flags.

### Migration from 0.2.x

| 0.2.x (removed) | Current |
|-----------------|---------|
| `repos-inventory` | `repo-ops inventory` |
| `repos-upgrade-plan` | `repo-ops plan` |
| `repos-upgrade-apply` | `repo-ops apply` |
| `repos-pushed` | `repo-ops pushed` |
| `repo_ops.commands.dispatch` | `repo_ops.cli` |
| `repo_ops.upgrade.ecosystems` | `repo_ops_upgrade.registry` (after `register_all()`) |

Reinstall operators: `uv tool install -e packages/repo-ops` (clone), `uv tool upgrade repo-ops` (PyPI), or the git `#subdirectory=` URL above. Update automation still calling `repos-*` or importing `repo_ops.upgrade.ecosystems`.

## Programmatic imports

| Area | Module |
|------|--------|
| CLI | `repo_ops.cli` |
| Upgrade entrypoints | `repo_ops_upgrade` (`main_plan`, `main_apply`) |
| Models | `repo_ops_models` |
| Discovery | `repo_ops_upgrade.discovery` |
| Ecosystem registry | `repo_ops_upgrade.registry` |
| GitHub Actions pins | `repo_ops_upgrade.actions` |
| Inventory | `repo_ops_inventory.scan` |
| Push audit | `repo_ops_pushed.audit` |
| Lockfiles | `repo_ops_lockfiles` |
| Node helpers | `repo_ops_js` |
| Shared | `repo_ops_core` |

Filesystem layout of `packages/*`: [AGENTS.md — Layout and files](AGENTS.md#layout-and-files).

## Verification and Testing

Full dev verification:

```bash
uv sync --locked --group dev
uv run python scripts/check_import_boundaries.py
uv run ruff check .
uv run ruff format --check .
uv run mypy packages/repo-ops-core/src packages/repo-ops-models/src packages/repo-ops-lockfiles/src packages/repo-ops-js/src packages/repo-ops-js-npm/src packages/repo-ops-js-pnpm/src packages/repo-ops-js-bun/src packages/repo-ops-js-yarn/src packages/repo-ops-go/src packages/repo-ops-rust/src packages/repo-ops-py/src packages/repo-ops-py-uv/src packages/repo-ops-py-poetry/src packages/repo-ops-py-pip-tools/src packages/repo-ops-py-hatch/src packages/repo-ops-upgrade/src packages/repo-ops-upgrade-remediations-js/src packages/repo-ops-inventory/src packages/repo-ops-pushed/src packages/repo-ops/src
uv run pytest -q --cov --cov-report=term-missing --cov-fail-under=15
uv build packages/repo-ops
```

Tests: `tests/`; config in root `pyproject.toml`. CI runs the same checks on Python 3.10–3.12; tag `v*` triggers [release.yml](.github/workflows/release.yml). When changing Markdown tiers, run the doc-audit skill on the repo root (e.g. `~/.cursor/skills/doc-audit` or `~/.claude/skills/doc-audit`).

Pull requests welcome via repository issues.
