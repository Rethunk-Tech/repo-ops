# repo-ops agent quick reference

Operator invocations for the `repo-ops` CLI. Changing repo-ops itself: the checkout [AGENTS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/AGENTS.md).

**Install and environment:** [HUMANS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md). **CLI flags (authoritative):** `repo-ops <subcommand> --help`. **Deep reference:** [docs/usage.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/usage.md), [docs/bulk-maintenance.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/bulk-maintenance.md).

## Run it directly

When the user names a command, run it — default roots apply when paths are omitted. Do not `which repo-ops`, read `src/`, or probe `--help` first; this file plus `--help` is enough.

| Goal | Command |
| ------ | --------- |
| Preview drift (read-only, exit 1 on drift) | `repo-ops plan --log-file plan.log` |
| Apply updates + verify (commits nothing; you commit after) | `repo-ops apply --log-file apply.log` |
| One checkout | `repo-ops plan --flat /path` then `repo-ops apply --flat /path` |
| JSON artifact | `repo-ops plan --json --json-out plan.json /path` |
| Stay in-range instead of the full sweep | `repo-ops apply --conservative` |
| Dependency footprint | `repo-ops inventory` |
| Fleet vulnerability sweep | `repo-ops audit` |
| Close superseded Dependabot PRs | `repo-ops dependabot` |
| SBOM / license scan | `repo-ops sbom` · `repo-ops license` |
| Toolchain/auth preflight | `repo-ops doctor` |

`plan`/`apply` run the **full sweep by default** — cross-range upgrades, per-package targeted bumps, Actions pin-to-SHA + digest refresh + cross-major, container tag + digest bumps. No flags to add. `--conservative` moves only what each manifest's constraints already allow. Submodule detection and children-first apply order are also on by default.

## DEFAULT_ROOTS

With no trailing `root_dir`, every subcommand except `install-skill` and `doctor` scans [`DEFAULT_ROOTS`](https://github.com/Rethunk-Tech/repo-ops/blob/main/src/repo_ops_core/constants.py): `/usr/local/src`, `~/Projects`, `~/Documents`, `~/Apps`. Pass explicit paths to narrow. `--flat` treats the root itself as one project instead of discovering the checkouts under it.

## Workflow

| Step | Rule |
| ---- | ---- |
| 1 | **`plan` before `apply`** on the same flags and roots, unless the user explicitly asked to apply. |
| 2 | **Dirty worktrees** are skipped; `--allow-dirty` only when intentional — never to sweep up work you did not mean to include. |
| 3 | **Verify failure** → `--on-verify-fail=revert` (default) rolls back every file apply changed and reinstalls; `keep` leaves them to inspect. Check toolchain, env, and flaky tests before blaming the bump. |
| 4 | **Commits** are fleet canon (`~/.claude/CLAUDE.md` § Git & GitHub); headless: `apply --commit=conventional\|agent`, preview with `--commit-dry-run`. Never push unless asked. |
| 5 | **Sweep the whole result.** `apply` leaves every repo it touched dirty. Enumerate them all, gate each, and commit each — a dirty repo after a sweep is this run's output. `--commit` gates only on an immutable install, so a repo's own gates still have to run when you commit by hand. |
| 6 | **Multi-repo playbook** — [docs/bulk-maintenance.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/bulk-maintenance.md). |
