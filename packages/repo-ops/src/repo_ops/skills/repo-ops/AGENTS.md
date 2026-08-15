# AI Agent Guidelines for repo-ops

Conventions and quick reference for AI coding agents (Cursor, Claude Code, etc.) working with the `repo-ops` CLI.

**Operators:** install and environment — [HUMANS.md](HUMANS.md). **Contributors:** commits, style, PR checks — [CONTRIBUTING.md](CONTRIBUTING.md). **Full flag reference:** `repo-ops <subcommand> --help` (authoritative; do not duplicate flag tables here).

## Quick reference

**When a user requests `repo-ops apply` (or `plan`, `inventory`, etc.), run it directly** — default roots apply when paths are omitted. Do not run `which repo-ops`, read `packages/` source, or open HUMANS.md to discover flags; this file and `repo-ops <subcommand> --help` are enough.

| User request | Run immediately |
| -------------- | ----------------- |
| `/repo-ops apply` | `repo-ops apply --log-file apply.log` |
| `repo-ops plan` (preview drift) | `repo-ops plan --log-file plan.log` |
| Update deps in one checkout | `repo-ops plan --flat /path` then `repo-ops apply --flat /path` |

`plan`/`apply` run the full-coverage sweep by default: cross-range upgrades for every ecosystem, per-package targeted bumps for every package manager, GitHub Actions pin-to-SHA + digest refresh + cross-major, and container image tag + digest bumps. No flags to add — it's just the default. Pass `--conservative` to stay in-range instead, moving only what each manifest's own constraints already allow.

### Subcommands

| Goal | Command |
| ------ | --------- |
| Dependency footprint (read-only) | `repo-ops inventory` |
| Plan drift (no writes) | `repo-ops plan` |
| Apply updates + verify | `repo-ops apply` |
| Pushed-commit audit | `repo-ops pushed` |
| Fleet vulnerability sweep | `repo-ops audit` |
| List/close superseded Dependabot PRs | `repo-ops dependabot` |
| Emit CycloneDX/SPDX SBOM | `repo-ops sbom` |
| Scan dependency licenses | `repo-ops license` |
| Toolchain/auth preflight | `repo-ops doctor` |
| Install bundled agent skill | `repo-ops install-skill` |

### DEFAULT_ROOTS

When no trailing `root_dir` arguments are passed, every subcommand except `install-skill` and `doctor` (`inventory`, `plan`, `apply`, `pushed`, `audit`, `dependabot`, `sbom`, `license`) scans [`DEFAULT_ROOTS`](packages/repo-ops-core/src/repo_ops_core/constants.py):

- `/usr/local/src`
- `~/Projects`
- `~/Documents`
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

# Apply after reviewing plan (same flags/roots as plan) — full sweep by default
repo-ops apply --log-file apply.log /path/to/root

# Stay in-range instead of the default full sweep
repo-ops apply --conservative --log-file apply.log

# Submodule umbrellas: plan first, apply children before parent
repo-ops plan --submodules --json --json-out plan.json /path/to/umbrella
repo-ops apply --submodule-order=children-first --submodules /path/to/umbrella
```

### Workflow

1. **`plan` before `apply`** — same flags and roots. Plan is read-only; exit `1` when actionable drift exists.
2. **Dirty worktrees** — skipped by default; use `--allow-dirty` only when intentional.
3. **Verify failures** — default `--on-verify-fail=keep` leaves changes; `--on-verify-fail=revert` rolls back lockfiles and tracked paths.
4. **Commits** — commit tooling is fleet canon (`~/.claude/CLAUDE.md` § Git & GitHub); headless: `apply --commit=conventional` or `--commit=agent` (preview with `--commit-dry-run`).
5. **Bulk multi-repo playbook** — [docs/bulk-maintenance.md](docs/bulk-maintenance.md).

### Flags cheat sheet

**Agent-critical flags only** — `repo-ops <subcommand> --help` is authoritative; do not mirror full flag tables here.

#### Shared output & discovery

| Flag | Applies to | Purpose |
| ------ | ------------ | --------- |
| `--json` | all subcommands except `sbom` | JSON report (`sbom` uses `--format`/`--output` instead) |
| `--json-out PATH` | inventory, plan, apply, pushed, doctor | Writes report atomically to `PATH`; stdout prints only that path |
| `--compact` | all subcommands | With `--json`: emit compact single-line output instead of pretty-printed |
| `--follow-symlinks` | plan, apply, pushed, audit, dependabot | Follow symlinks when discovering git repos (default: do not) |

#### `plan` / `apply` flags

| Flag | Applies to | Purpose |
| ------ | ------------ | --------- |
| `--flat` | inventory, plan, apply | Single-repo root (no org/proj walk) |
| `--filter ECO` | inventory, plan, apply | Limit to one ecosystem: `node`, `go`, `rust`, `py`, `container`, `precommit`, `actions`, or `deno` |
| `--manager MANAGER` | plan, apply | Limit to one package manager |
| `--conservative` | plan, apply | Stay in-range: only bump deps their own manifest constraints already allow. Default (no flag) is the full sweep — cross-range upgrades, per-package targeted bumps, Actions pin-to-SHA/digest/major, and container tag/digest bumps |
| `--allow-dirty` | plan, apply | Process dirty git worktrees |
| `--skip-actions` | plan, apply | Dependency-only (no Actions pin audit) |
| `--log-file PATH` | plan, apply | Combined stdout/stderr transcript |
| `--migrate-to-uv-lock` | plan, apply | Opt into creating `uv.lock` for Hatch projects that lack one |
| `--submodules` | plan, apply | Include submodule metadata in JSON |
| `--submodule-order=children-first` | apply | Process child repos before parent umbrellas |
| `--no-verify` | apply | Skip verify gates |
| `--on-verify-fail=revert\|keep` | apply | Roll back or keep on verify failure |
| `--commit=none\|conventional\|agent` | apply | Post-apply commits |
| `--only-outdated` | plan, apply | Filter human-readable output to outdated entries; JSON remains unfiltered |
| `--skip-remediations IDS` | plan, apply | Skip selected remediation IDs; see [usage.md](docs/usage.md) for the current catalog |
| `--exclude PATTERN` | plan, apply, audit, dependabot, pushed | Exclude repos matching pattern (repeatable) |
| `--go-upgrade-mode MODE` | plan, apply | Go upgrade policy override: `patch`, `explicit`, or `mvs`. Unset default follows sweep mode: `explicit` under the default full sweep, `patch` under `--conservative` |
| `--actions-upgrade-mode MODE` | plan, apply | Actions cross-major policy override: `normalize` or `latest`. Unset default follows sweep mode: `latest` under the default full sweep, `normalize` under `--conservative` |
| `--actions-tag-style STYLE` | plan, apply | Tag style when unpinning SHA pins: `major` (default) or `exact` |
| `--actions-unpin-sha` | plan, apply | Resolve SHA-pinned Actions to version tags on apply (independent of the sweep default; SHA pins stay preserved either way unless this is passed) |
| `--actions-org-policy` | plan, apply | Fetch org allowed-actions policy and flag violations |
| `--actions-policy-level block\|warn\|report` | plan, apply | How org-policy violations gate pin rewrites (default `report`) |
| `--actions-lint-level block\|warn\|off` | plan, apply | Actionlint gate policy |

#### `pushed` flags

| Flag | Purpose |
| ------ | --------- |
| `--hours HOURS` | Rolling window in hours (default: 24) |
| `--since ISO8601` | Window start (timezone required, e.g. `2026-05-26T00:00Z`) |
| `--until ISO8601` | Window end (timezone required; defaults to now) |
| `--source {local,gh}` | Attribution source: local remote-tracking reflog (default) or authenticated GitHub PushEvents |
| `--show-shas` | Print commit SHAs (8 chars) grouped by repo after the table |
| `--show-messages` | Implies `--show-shas`; also print each commit subject (max 100 chars) |
| `--count-only` | Print only the total commit count |
| `--exclude PATTERN` | Exclude repos matching pattern (repeatable) |

#### `inventory` flags

| Flag | Purpose |
| ------ | --------- |
| `--sort {name,direct,versions,on-disk,edges}` | Sort column (default: `name` asc; others descending) |
| `--no-cache` | Bypass XDG Go module result cache |

#### `audit` flags

Default scan is read-only; `--fix` applies remediations (no auto-commit).

| Flag | Purpose |
| ------ | --------- |
| `--fail-on SEVERITY` | Exit 1 when any workspace has a vulnerability at or above `SEVERITY` (`critical`, `high`, `moderate`, `low`, `never`); default `high` |
| `--fail-on-unknown` | Exit 1 when any workspace has findings with unknown severity (govulncheck); default off |
| `--fix-plan` | Read-only remediation list; with `--json` populates `remediations` key; exit 1 when actionable |
| `--fix` | Apply remediations via `repo-ops-upgrade` orchestration; skips dirty worktrees unless `--allow-dirty` |
| `--sarif-out PATH` | Write SARIF 2.1.0 output for GitHub code-scanning |
| `--exclude PATTERN` | Exclude repos matching pattern (repeatable) |

#### `dependabot` flags

| Flag | Purpose |
| ------ | --------- |
| `--close` | Close superseded Dependabot PRs via `gh pr close` (default: dry-run) |
| `--scaffold` | Generate `.github/dependabot.yml` entries for detected ecosystems (default: dry-run) |
| `--write` | With `--scaffold`: write the file (requires `--scaffold`) |
| `--schedule INTERVAL` | Schedule for scaffolded entries: `daily`, `weekly` (default), or `monthly` |
| `--exclude PATTERN` | Exclude repos matching pattern (repeatable) |

#### `sbom` flags

| Flag | Purpose |
| ------ | --------- |
| `--format FORMAT` | SBOM format: `cyclonedx` (default) or `spdx` |
| `--output PATH` | Write SBOM to `PATH` instead of stdout |
| `--enrich-licenses` | Opt-in SPDX fill from on-disk/registry metadata (also `REPO_OPS_ENRICH_LICENSES`) |
| `--enrich-licenses-local` | Local/cache-only SPDX enrichment (also `REPO_OPS_ENRICH_LICENSES_LOCAL`; no registry network) |
| `--enrich-licenses-network` | Force registry network enrichment even when LOCAL env/flag is set; wins over `--enrich-licenses-local` (`REPO_OPS_ENRICH_LICENSES_NETWORK`) |

#### `license` flags

| Flag | Purpose |
| ------ | --------- |
| `--allow LICENSES` | Comma-separated SPDX IDs permitted; others flagged |
| `--deny LICENSES` | Comma-separated SPDX IDs forbidden |
| `--enrich-licenses` | Opt-in SPDX enrichment (same as `sbom`; also `REPO_OPS_ENRICH_LICENSES`) |
| `--enrich-licenses-local` | Local/cache-only enrichment (same as `sbom`; also `REPO_OPS_ENRICH_LICENSES_LOCAL`) |
| `--enrich-licenses-network` | Force registry network enrichment even when LOCAL env/flag is set; wins over `--enrich-licenses-local` (`REPO_OPS_ENRICH_LICENSES_NETWORK`) |
| `--no-fail-unknown` | Exit 0 even when unknown licenses are found (default: exit 2) |

#### `install-skill` flags

| Flag | Purpose |
| ------ | --------- |
| `--target {cursor,claude,both}` | Where to install (default: `both`) |
| `--check` | Check installed skill copies for drift without writing; exits 1 on drift |
| `--force` | Overwrite existing skill directories |
| `--home DIR` | Override home directory (also `REPO_OPS_HOME` env) |
| `--dest DIR` | Install directly to this path (testing; ignores `--target` layout) |
| `--dry-run` | Print planned installs without writing files |

#### `doctor` flags

| Flag | Purpose |
| ------ | --------- |
| `--strict` | Exit 1 when any probed binary is missing (default: exit 0 regardless) |
| `--json` | JSON report (see shared output flags above) |
| `--compact` | With `--json`: compact single-line output |
| `--json-out PATH` | With `--json`: write report atomically to `PATH` |

`doctor` is read-only and takes no `root_dir` arguments (no scan roots in v1). It probes the binaries repo-ops shells out to (git, gh, uv, hatch, pyrefly, ruff, pip-compile, poetry, pipenv, pdm, pip-audit, pre-commit, npm, pnpm, yarn, bun, corepack, deno, go, gofmt, govulncheck, cargo, cargo-audit via `cargo audit --version`, actionlint) for found/missing, resolved path, and version, plus a `gh auth status` check.
It also reports installed Cursor/Claude skill-copy drift; JSON reports expose
those entries in the `skill_drift` field.

Discovery and security: [docs/usage.md — Security and discovery](docs/usage.md#security-and-discovery).

### What NOT to do

- Do **not** explore the repo (`grep` flags in source, read `run.py` / `cli.py`, skim HUMANS) before routine `plan`/`apply` — use this quick reference and `--help`.
- Do **not** run `which repo-ops` plus multiple `--help` calls when the user already named a subcommand and flag.
- Do **not** run `apply` without `plan` on the same flags/roots when drift is unknown **and** the user did not explicitly ask to apply.
- Do **not** use `--allow-dirty` to mask uncommitted work you did not intend to include.
- Do **not** duplicate CLI flag tables into README, AGENTS, or the bundled SKILL — `--help` is canonical.
- Do **not** push unless the user explicitly requests it.
- Do **not** assume verify failure means bad dependency bumps — check toolchain, env, and flaky tests first.

## Coordination with other agents

- Multiple agents often work concurrently on refactors and extractions.
- Before editing, run MCP `git_status` to see files others are changing.
- Avoid broad edits across `packages/repo-ops-upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- Prefer small, reviewable changes. Batch related edits where safe.

## Layout and files

Full package map and plugin registration: [docs/architecture.md](docs/architecture.md).

**uv workspace** (`packages/*`): installable meta package `packages/repo-ops` (`repo-ops` on PyPI); repo root is not installable (`package = false`). Console script **`repo-ops`** dispatches to `inventory`, `plan`, `apply`, `pushed`, `install-skill`, `audit`, `dependabot`, `sbom`, `license`, `doctor`.

## Agent invariants (behavior)

When changing behavior, keep these aligned with code and docs:

- **Plan vs apply:** plan/check is read-only for lockfiles and workflows; plan may run `corepack prepare` (network) for Yarn checks; Node Corepack runs on apply (`repo_ops_upgrade.registry.apply_workspace`) for npm/pnpm/yarn only — Corepack has no bun shim, so bun workspaces skip it and use the `bun` on PATH.
- **SHA-pinned Actions:** never unpinned to a floating tag unless `--actions-unpin-sha` is passed — that default is unaffected by the sweep. Digest refresh (bump a SHA-pinned Action to the newest release's commit within its current major) and pin-to-SHA (convert a floating-tag Action to a SHA pin) both run by default as part of the full sweep; `--conservative` turns both off and leaves SHA pins report-only. Cross-major digest re-pin (supersedes Dependabot major-bump PRs) also runs by default, updating the SHA and `# vN` comment to the new major; `--actions-upgrade-mode=normalize` keeps Actions within-major while leaving the rest of the sweep (other ecosystems, and Actions' own digest refresh/pin-to-SHA) unaffected.
- **Floating (never-SHA-pinned) Actions:** converted to a SHA pin by default as part of the sweep — resolved to the commit SHA of the highest `vN.M.P` release in the pin's current major, adding/replacing a `# vN.M.P` comment; crosses major the same way digest refresh does. `--conservative` leaves floating tags untouched.
- **SHA-pinned pre-commit and submodule refs:** digest-refreshed to the newest release within their tagged major by default, without changing their upstream repository or URL intent; `--conservative` leaves them report-only.
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

## Cutting a release

Releases cut on `v*` tag push — `release.yml`'s `build` job creates the GitHub Release and uploads `dist/*`.

1. `uv run python scripts/set_version.py bump patch` — bumps all 28 workspace manifests in lockstep (no args = verify no drift).
2. Update `CHANGELOG.md`: `## [Unreleased]` → `## [Unreleased]` + `## [X.Y.Z] - <date>`. **Audit the section against `git log vPREV..HEAD`** — the prior batch's consolidation often misses late-landing commits.
3. Commit `chore(release): vX.Y.Z` naming exactly `pyproject.toml packages/*/pyproject.toml CHANGELOG.md uv.lock`. `uv sync --locked --group dev` must pass (CI gate).
4. Full gate firsthand: `uv run pytest -p no:cacheprovider -q` + `uv run ruff check .`.
5. Annotated tag `git tag -a vX.Y.Z`; push commit, then tag (FF-only).
6. `release.yml` extracts the Keep-a-Changelog `## [X.Y.Z]` section for
   `vX.Y.Z` and publishes it as the release notes; the build uploads `dist/*`
   only. When those are the only automated notes and assets, no manual
   `gh release edit` or `gh release delete-asset` cleanup is needed.
7. Reinstall: `uv tool install --force ./packages/repo-ops` (uv tool, not pipx). Verify `repo-ops --version` and smoke any new subcommands.

**PyPI publish is intentionally deferred** (issue #124, open). OIDC trusted-publisher exchange fails with "valid token, but no corresponding publisher" for claims `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`, `workflow_ref: .../release.yml`, `environment: pypi`. Fix is an operator action on PyPI's publishing settings — **do not re-diagnose as a CI defect.**
