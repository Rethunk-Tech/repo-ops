# AI Agent Guidelines for repo-ops

Conventions and quick reference for AI coding agents (Cursor, Claude Code, etc.) working with the `repo-ops` CLI.

**Operators:** install and environment — [HUMANS.md](HUMANS.md). **Contributors:** commits, style, PR checks — [CONTRIBUTING.md](CONTRIBUTING.md). **Full flag reference:** `repo-ops <subcommand> --help` (authoritative; do not duplicate flag tables here).

## Quick reference

**When a user requests `repo-ops apply --latest` (or `plan`, `inventory`, etc.), run it directly** — default roots apply when paths are omitted. Do not run `which repo-ops`, read `packages/` source, or open HUMANS.md to discover flags; this file and `repo-ops <subcommand> --help` are enough.

| User request | Run immediately |
|--------------|-----------------|
| `/repo-ops apply --latest` | `repo-ops apply --latest --log-file apply.log` |
| `repo-ops plan --latest` (preview drift) | `repo-ops plan --latest --log-file plan.log` |
| Update deps in one checkout | `repo-ops plan --flat /path` then `repo-ops apply --flat /path` |

### Subcommands

| Goal | Command |
|------|---------|
| Dependency footprint (read-only) | `repo-ops inventory` |
| Plan drift (no writes) | `repo-ops plan` |
| Apply updates + verify | `repo-ops apply` |
| Pushed-commit audit | `repo-ops pushed` |
| Fleet vulnerability sweep | `repo-ops audit` |
| List/close superseded Dependabot PRs | `repo-ops dependabot` |
| Emit CycloneDX/SPDX SBOM | `repo-ops sbom` |
| Scan dependency licenses | `repo-ops license` |
| Install bundled agent skill | `repo-ops install-skill` |

### DEFAULT_ROOTS

When no trailing `root_dir` arguments are passed, `inventory`, `plan`, `apply`, and `pushed` scan [`DEFAULT_ROOTS`](packages/repo-ops-core/src/repo_ops_core/constants.py):

- `/usr/local/src`
- `~/Projects`
- `~/Documents`
- `~/bastion-ai-helpers`
- `~/Apps`

Pass explicit checkout paths to limit scope. Use `--flat` when a root is a single repo (skip org/proj two-level walk).

### Essential invocations

```bash
# Read-only footprint for one checkout
repo-ops inventory --flat /path/to/checkout

# Plan all DEFAULT_ROOTS (exit 1 when drift exists — CI-friendly)
repo-ops plan --log-file plan.log

# Plan one tree with JSON artifact
repo-ops plan --json --json-out plan.json /path/to/root

# Apply after reviewing plan (same flags/roots as plan)
repo-ops apply --log-file apply.log /path/to/root

# Aggressive cross-range upgrades (see --latest below)
repo-ops plan --latest
repo-ops apply --latest --log-file apply.log

# Submodule umbrellas: plan first, apply children before parent
repo-ops plan --submodules --json --json-out plan.json /path/to/umbrella
repo-ops apply --submodule-order=children-first --submodules /path/to/umbrella
```

### Workflow

1. **`plan` before `apply`** — same flags and roots. Plan is read-only; exit `1` when actionable drift exists.
2. **Dirty worktrees** — skipped by default; use `--allow-dirty` only when intentional.
3. **Verify failures** — default `--on-verify-fail=keep` leaves changes; `--on-verify-fail=revert` rolls back lockfiles and tracked paths.
4. **Commits** — use MCP `batch_commit` (`push: "never"`) in IDE sessions; headless: `apply --commit=conventional` or `--commit=agent` (preview with `--commit-dry-run`).
5. **Bulk multi-repo playbook** — [docs/bulk-maintenance.md](docs/bulk-maintenance.md).

### `--latest` umbrella

`--latest` opts into aggressive / cross-range upgrades for this run:

- Sets `REPO_OPS_BUN_UPDATE_LATEST`, `REPO_OPS_PNPM_UPDATE_LATEST`, `REPO_OPS_YARN_UPDATE_LATEST`
- Defaults `REPO_OPS_GO_UPGRADE_MODE` to `explicit` when unset
- Sets GitHub Actions to `--actions-upgrade-mode=latest`

Per-ecosystem overrides: explicit `--go-upgrade-mode` or `--actions-upgrade-mode` wins over the umbrella for that ecosystem. npm has no latest mode (semver ranges only). Details: `repo-ops plan --help` and [docs/usage.md — Ecosystem upgrade modes](docs/usage.md#ecosystem-upgrade-modes-plan-and-apply).

### Flags cheat sheet

Selected flags — `repo-ops <subcommand> --help` is always authoritative.

#### `plan` / `apply` flags

| Flag | Applies to | Purpose |
|------|------------|---------|
| `--flat` | inventory, plan, apply | Single-repo root (no org/proj walk) |
| `--filter ECO` | inventory, plan, apply | Limit to one ecosystem: `node`, `go`, `rust`, `py`, `container`, `precommit`, `actions`, or `deno` |
| `--manager MANAGER` | plan, apply | Limit to one package manager |
| `--latest` | plan, apply | Aggressive cross-range upgrades (see above) |
| `--allow-dirty` | plan, apply | Process dirty git worktrees |
| `--skip-actions` | plan, apply | Dependency-only (no Actions pin audit) |
| `--json` / `--json-out PATH` | all subcommands | JSON report (`--json-out` writes atomically; stdout prints path) |
| `--compact` | all subcommands | With `--json`: emit compact single-line output instead of pretty-printed |
| `--log-file PATH` | plan, apply | Combined stdout/stderr transcript |
| `--log-level {debug,info,warning,error}` | plan, apply | Set stdlib root logger level; `--verbose` / `-v` implies `debug` when absent |
| `--strict-exit` | plan, apply | Exit 1 on any skip or fail |
| `-q` / `--quiet` | plan, apply | Suppress per-workspace progress lines (summary still prints) |
| `--only-outdated` | plan, apply | Omit clean workspaces from human output |
| `--max-failures N` | plan, apply | Max failure lines in end-of-run summary (default: 20) |
| `--fail-fast` | plan, apply | Stop scheduling new workspaces after the first failure |
| `--yarn-jobs N` | plan, apply | Cap concurrent Yarn isolated-copy extractions (default: 2; prevents ENOSPC on large monorepos) |
| `--migrate-to-uv-lock` | plan, apply | Opt into creating `uv.lock` for Hatch projects that lack one |
| `--submodules` | plan, apply | Include submodule metadata in JSON |
| `--submodule-order=children-first` | apply | Process child repos before parent umbrellas |
| `--no-verify` | apply | Skip verify gates |
| `--verify-test` | apply | Run `go test ./...` during verify gates |
| `--on-verify-fail=revert\|keep` | apply | Roll back or keep on verify failure |
| `--commit=none\|conventional\|agent` | apply | Post-apply commits |
| `--exclude PATTERN` | plan, apply | Exclude repos matching pattern (repeatable) |
| `--actions-lint-level block\|warn\|off` | plan, apply | Actionlint gate policy |

#### `pushed` flags

| Flag | Purpose |
|------|---------|
| `--hours HOURS` | Rolling window in hours (default: 24) |
| `--since ISO8601` | Window start (timezone required, e.g. `2026-05-26T00:00Z`) |
| `--until ISO8601` | Window end (timezone required; defaults to now) |
| `--show-shas` | Print commit SHAs (8 chars) grouped by repo after the table |
| `--show-messages` | Implies `--show-shas`; also print each commit subject (max 100 chars) |
| `--count-only` | Print only the total commit count |

#### `inventory` flags

| Flag | Purpose |
|------|---------|
| `--sort {name,direct,versions,on-disk,edges}` | Sort column (default: `name` asc; others descending) |
| `--no-cache` | Bypass XDG Go module result cache |

#### `audit` flags

| Flag | Purpose |
|------|---------|
| `--fail-on-unknown` | Exit 1 when any workspace has findings with unknown severity (govulncheck); default off |

#### `install-skill` flags

| Flag | Purpose |
|------|---------|
| `--target {cursor,claude,both}` | Where to install (default: `both`) |
| `--force` | Overwrite existing skill directories |
| `--home DIR` | Override home directory (also `REPO_OPS_HOME` env) |
| `--dest DIR` | Install directly to this path (testing; ignores `--target` layout) |

Discovery and security: [docs/usage.md — Security and discovery](docs/usage.md#security-and-discovery).

### What NOT to do

- Do **not** explore the repo (`grep` flags in source, read `run.py` / `cli.py`, skim HUMANS) before routine `plan`/`apply` — use this quick reference and `--help`.
- Do **not** run `which repo-ops` plus multiple `--help` calls when the user already named a subcommand and flag.
- Do **not** run `apply` without `plan` on the same flags/roots when drift is unknown **and** the user did not explicitly ask to apply.
- Do **not** use `--allow-dirty` to mask uncommitted work you did not intend to include.
- Do **not** duplicate CLI flag tables into README, AGENTS, or the bundled SKILL — `--help` is canonical.
- Do **not** use shell `git commit` in agent sessions — MCP `batch_commit` per [CONTRIBUTING.md — Git for agents](CONTRIBUTING.md#git-for-agents).
- Do **not** push unless the user explicitly requests it.
- Do **not** assume verify failure means bad dependency bumps — check toolchain, env, and flaky tests first.

## Coordination with other agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, run MCP `git_status` to see files others are changing.
- Avoid broad edits across `packages/repo-ops-upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- Prefer small, reviewable changes. Batch related edits where safe.

## Layout and files

Full package map and plugin registration: [docs/architecture.md](docs/architecture.md).

**uv workspace** (`packages/*`): installable meta package `packages/repo-ops` (`repo-ops` on PyPI); repo root is not installable (`package = false`). Console script **`repo-ops`** dispatches to `inventory`, `plan`, `apply`, `pushed`, `install-skill`, `audit`, `dependabot`, `sbom`, `license`.

## Agent invariants (behavior)

When changing behavior, keep these aligned with code and docs:

- **Plan vs apply:** plan/check is read-only for lockfiles and workflows; plan may run `corepack prepare` (network) for Yarn checks; Node Corepack runs on apply (`repo_ops_upgrade.registry.apply_workspace`).
- **SHA-pinned Actions:** preserved by default (report only); unpin to tags only with `--actions-unpin-sha`. Digest bumps within major: `--actions-refresh-sha`. Cross-major digest re-pin (supersedes Dependabot major-bump PRs): `--actions-refresh-sha --latest` (or `--actions-upgrade-mode=latest`); updates SHA and `# vN` comment to the new major.
- **Discovery:** `resolve_scan_roots`, optional `--follow-symlinks`, `--flat`, actions-only repos — [docs/usage.md — Security and discovery](docs/usage.md#security-and-discovery).
- **Docs tiers:** README (orientation + Documentation table), HUMANS (quickstart + doc links), docs/ (deep reference), this file (agent quick reference + coordination), CONTRIBUTING (commits/style).

## Development practices for agents

- Run [docs/testing.md — Verification and Testing](docs/testing.md) before commits (at least `pytest` for code changes).
- Avoid committing `.venv/`, `dist/`, or other build artifacts.
- When adding CLI or plugins, update `repo_ops._register` and tiered docs (not duplicate flag tables).
- Respect `.editorconfig` (4-space Python, LF, final newlines).
- When scope is unclear, ask in-session rather than guessing.

## Hygiene and tooling

- `.gitignore` covers Python caches, venvs, build artifacts, coverage, and IDE noise.
