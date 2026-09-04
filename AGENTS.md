# repo-ops — agent primer

Operators: [HUMANS.md](HUMANS.md). Contributors: [CONTRIBUTING.md](CONTRIBUTING.md). Flags: `repo-ops <subcommand> --help` is authoritative — do not duplicate flag tables here.

Run the requested command directly. Do not `which repo-ops`, grep `src/` for flags, or open HUMANS.md first.

## Commands

| Goal | Command |
| ------ | --------- |
| Preview drift (read-only, exit 1) | `repo-ops plan --log-file plan.log` |
| Apply + verify | `repo-ops apply --log-file apply.log` |
| One checkout | `repo-ops plan --flat /path` then `apply --flat /path` |
| JSON artifact | `repo-ops plan --json --json-out plan.json /path` |
| Footprint | `repo-ops inventory` |
| Audit / deps | `repo-ops audit` · `repo-ops dependabot` · `repo-ops sbom` · `repo-ops license` |
| Preflight | `repo-ops doctor` · `repo-ops install-skill` |

Full sweep by default on `plan`/`apply`; `--conservative` stays in-range. No trailing `root_dir`: scans [`DEFAULT_ROOTS`](src/repo_ops_core/constants.py) except `install-skill`/`doctor`. `--flat` = one project root.

## Workflow

1. **`plan` before `apply`** on same flags/roots unless user asked to apply.
2. **Dirty worktrees** skipped; `--allow-dirty` only when intentional.
3. **Verify failure** → `--on-verify-fail=revert` (default) rolls back; check toolchain before blaming the bump.
4. **Commits** — fleet canon (`~/.claude/CLAUDE.md` § Git & GitHub); headless: `apply --commit=conventional|agent`.
5. Multi-repo: [docs/bulk-maintenance.md](docs/bulk-maintenance.md).

## Layout

Module map: [docs/architecture.md](docs/architecture.md). Imports: [docs/programmatic-imports.md](docs/programmatic-imports.md). Single wheel (`repo_ops_*` modules ship via `module-name` in `pyproject.toml`).

## Invariants

- **Plan is read-only** for lockfiles/workflows; may run `corepack prepare` (network) for Yarn. Corepack on apply for npm/pnpm/yarn only — not bun.
- **SHA-pinned Actions** never unpinned unless `--actions-unpin-sha`; digest refresh and cross-major re-pin run by default. `--conservative` turns Actions updates off.
- **Floating Actions** → SHA pins at highest `vN.M.P` in current major.
- **pre-commit / submodule SHA refs** — digest refresh within tagged major only.
- Discovery: [docs/usage.md § Security and discovery](docs/usage.md#security-and-discovery).

## Development

`uv run pytest` before commit. New CLI/plugin: update `repo_ops._register` and tiered docs. Concurrent agents: check repo state; small reviewable diffs.
