# AI Agent Guidelines for repo-ops

Conventions and quick reference for AI coding agents (Cursor, Claude Code, etc.) working with the `repo-ops` CLI.

**Operators:** install and environment — [HUMANS.md](HUMANS.md). **Contributors:** commits, style, PR checks — [CONTRIBUTING.md](CONTRIBUTING.md). **Full flag reference:** `repo-ops <subcommand> --help` (authoritative; do not duplicate flag tables here).

## Quick reference

**When a user requests `repo-ops apply` (or `plan`, `inventory`, etc.), run it directly** — default roots apply when paths are omitted. Do not run `which repo-ops`, read `src/` source, or open HUMANS.md to discover flags; this file and `repo-ops <subcommand> --help` are enough.

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
| Branch / dirty / ahead / behind per repo | `repo-ops inventory --status` |
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

When no trailing `root_dir` arguments are passed, every subcommand except `install-skill` and `doctor` (`inventory`, `plan`, `apply`, `pushed`, `audit`, `dependabot`, `sbom`, `license`) scans [`DEFAULT_ROOTS`](src/repo_ops_core/constants.py):

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

# Submodule umbrellas: submodule detection + children-first apply order are on by default
repo-ops plan --json --json-out plan.json /path/to/umbrella
repo-ops apply /path/to/umbrella
```

### Workflow

1. **`plan` before `apply`** — same flags and roots. Plan is read-only; exit `1` when actionable drift exists.
2. **Dirty worktrees** — skipped by default; use `--allow-dirty` only when intentional.
3. **Verify failures** — default `--on-verify-fail=revert` rolls back apply-artifact files (lockfiles and manifests); `--on-verify-fail=keep` leaves them for inspection.
4. **Commits** — commit tooling is fleet canon (`~/.claude/CLAUDE.md` § Git & GitHub); headless: `apply --commit=conventional` or `--commit=agent` (preview with `--commit-dry-run`).
5. **Bulk multi-repo playbook** — [docs/bulk-maintenance.md](docs/bulk-maintenance.md).

### Flags cheat sheet

Full flag reference: `repo-ops <subcommand> --help` (authoritative); flags in depth: [docs/usage.md](docs/usage.md). Discovery and security: [docs/usage.md — Security and discovery](docs/usage.md#security-and-discovery).

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
- Avoid broad edits across `src/repo_ops_upgrade*` and `tests/*` during active refactor passes unless hygiene-critical and coordinated.
- Prefer small, reviewable changes. Batch related edits where safe.

## Layout and files

Full module map and plugin registration: [docs/architecture.md](docs/architecture.md).

**One distribution** (`repo-ops`, built from `src/`). The top-level `repo_ops_*` modules are a code-layout choice, not separate installables — they all ship in the same wheel via the `module-name` list in `pyproject.toml`. Console script **`repo-ops`** dispatches to `inventory`, `plan`, `apply`, `pushed`, `install-skill`, `audit`, `dependabot`, `sbom`, `license`, `doctor`.

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

1. `uv version --bump patch` — there is one manifest, so there is no drift to check.
2. Update `CHANGELOG.md`: `## [Unreleased]` → `## [Unreleased]` + `## [X.Y.Z] - <date>`. **Audit the section against `git log vPREV..HEAD`** — the prior batch's consolidation often misses late-landing commits.
3. Commit `chore(release): vX.Y.Z` naming exactly `pyproject.toml CHANGELOG.md uv.lock`. `uv sync --locked --group dev` must pass (CI gate).
4. Full gate firsthand: `uv run pytest -p no:cacheprovider -q` + `uv run ruff check .`.
5. Annotated tag `git tag -a vX.Y.Z`; push commit, then tag (FF-only).
6. `release.yml` extracts the Keep-a-Changelog `## [X.Y.Z]` section for
   `vX.Y.Z` and publishes it as the release notes; the build uploads `dist/*`
   only. When those are the only automated notes and assets, no manual
   `gh release edit` or `gh release delete-asset` cleanup is needed.
7. Reinstall: `uv tool install --force .` (uv tool, not pipx). Verify `repo-ops --version` and smoke any new subcommands.

**PyPI publish is intentionally deferred** (issue #124, open). OIDC trusted-publisher exchange fails with "valid token, but no corresponding publisher" for claims `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`, `workflow_ref: .../release.yml`, `environment: pypi`. Fix is an operator action on PyPI's publishing settings — **do not re-diagnose as a CI defect.**
