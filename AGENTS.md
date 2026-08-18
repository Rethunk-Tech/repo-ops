# AI Agent Guidelines for repo-ops

Quick reference for AI coding agents working with the `repo-ops` CLI.

**Operators:** [HUMANS.md](HUMANS.md). **Contributors:** [CONTRIBUTING.md](CONTRIBUTING.md). **Flags:** `repo-ops <subcommand> --help` is authoritative — do not duplicate flag tables here or in README/SKILL.

## Quick reference

Run the requested command directly. Do not `which repo-ops`, grep `src/` for flags, or open HUMANS.md first — this file plus `--help` is enough.

| Goal | Command |
| ------ | --------- |
| Preview drift (read-only, exit 1 on drift) | `repo-ops plan --log-file plan.log` |
| Apply updates + verify | `repo-ops apply --log-file apply.log` |
| One checkout | `repo-ops plan --flat /path` then `repo-ops apply --flat /path` |
| JSON artifact | `repo-ops plan --json --json-out plan.json /path` |
| Dependency footprint | `repo-ops inventory` |
| Branch / dirty / ahead / behind per repo | `repo-ops inventory --status` |
| Pushed-commit audit | `repo-ops pushed` |
| Fleet vulnerability sweep | `repo-ops audit` |
| Close superseded Dependabot PRs | `repo-ops dependabot` |
| SBOM / license scan | `repo-ops sbom` · `repo-ops license` |
| Toolchain/auth preflight | `repo-ops doctor` |
| Install bundled agent skill | `repo-ops install-skill` |

`plan`/`apply` run the **full sweep by default** — cross-range upgrades, targeted bumps, Actions pin-to-SHA + digest refresh + cross-major, container tag + digest bumps. No flags to add. `--conservative` stays in-range instead.

### DEFAULT_ROOTS

With no trailing `root_dir`, every subcommand except `install-skill` and `doctor` scans [`DEFAULT_ROOTS`](src/repo_ops_core/constants.py): `/usr/local/src`, `~/Projects`, `~/Documents`, `~/Apps`. Pass explicit paths to narrow. `--flat` treats the root itself as one project instead of discovering the checkouts under it.

### Workflow

1. **`plan` before `apply`** on the same flags and roots, unless the user explicitly asked to apply.
2. **Dirty worktrees** are skipped; `--allow-dirty` only when intentional, never to sweep up unrelated work.
3. **Verify failure** → `--on-verify-fail=revert` (default) rolls back lockfiles and manifests; `keep` leaves them to inspect. Check toolchain, env, and flaky tests before blaming the bump.
4. **Commits** are fleet canon (`~/.claude/CLAUDE.md` § Git & GitHub); headless: `apply --commit=conventional|agent`, preview with `--commit-dry-run`.
5. **Multi-repo playbook** — [docs/bulk-maintenance.md](docs/bulk-maintenance.md).

## Layout

Module map: [docs/architecture.md](docs/architecture.md). Import paths: [docs/programmatic-imports.md](docs/programmatic-imports.md).

**One distribution** (`repo-ops`, built from `src/`). The top-level `repo_ops_*` modules are a code-layout choice, not separate installables — they all ship in the same wheel via `module-name` in `pyproject.toml`.

## Agent invariants

Keep these aligned with code when changing behavior:

- **Plan is read-only** for lockfiles and workflows, but may run `corepack prepare` (network) for Yarn checks. Corepack runs on apply for npm/pnpm/yarn only — it has no bun shim, so bun workspaces use the `bun` on PATH.
- **SHA-pinned Actions are never unpinned** to a floating tag unless `--actions-unpin-sha` is passed; the sweep does not change that. Digest refresh, pin-to-SHA, and cross-major re-pin (which supersedes Dependabot major bumps) all run by default. `--conservative` turns them off and leaves pins report-only; `--actions-upgrade-mode=normalize` keeps Actions within-major without affecting the rest of the sweep.
- **Floating Actions are converted to SHA pins** by default, resolved to the highest `vN.M.P` release in the current major with a matching `# vN.M.P` comment.
- **SHA-pinned pre-commit and submodule refs** are digest-refreshed within their tagged major, never repointed at a different upstream.
- **Discovery** — [docs/usage.md § Security and discovery](docs/usage.md#security-and-discovery).

## Development

- Run [verification](docs/testing.md) before committing — at minimum `uv run pytest` for code changes.
- Adding a CLI or plugin: update `repo_ops._register` and the tiered docs.
- Doc tiers: README (orientation), HUMANS (run/use), docs/ (deep reference), this file (agent quick reference), CONTRIBUTING (commits/style).
- Concurrent agents are common: check repo state before editing, and prefer small reviewable changes over broad passes across `src/repo_ops_upgrade*`.

## Cutting a release

Releases cut on `v*` tag push — `release.yml` creates the GitHub Release and uploads `dist/*`, extracting the `## [X.Y.Z]` CHANGELOG section as the notes.

1. `uv version --bump patch` (or `minor` for a feature).
2. `CHANGELOG.md`: `## [Unreleased]` → `## [Unreleased]` + `## [X.Y.Z] - <date>`. **Audit against `git log vPREV..HEAD`** — consolidation often misses late-landing commits.
3. Commit `chore(release): vX.Y.Z` naming exactly `pyproject.toml CHANGELOG.md uv.lock`; `uv sync --locked --group dev` must pass.
4. Full gate firsthand: `uv run pytest -p no:cacheprovider -q` + `uv run ruff check .`.
5. Annotated tag `git tag -a vX.Y.Z`; push commit, then tag (FF-only).
6. Reinstall `uv tool install --force .` (uv tool, not pipx); verify `repo-ops --version` and smoke new subcommands.

**PyPI publish is intentionally deferred** ([#124](https://github.com/Rethunk-Tech/repo-ops/issues/124)). OIDC exchange fails with "valid token, but no corresponding publisher" — an operator action on PyPI's publishing settings, **not a CI defect; do not re-diagnose.**
