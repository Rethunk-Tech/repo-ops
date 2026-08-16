# repo-ops agent quick reference

Operator invocations for the `repo-ops` CLI. Named `REFERENCE.md` (not
`AGENTS.md`) so a skill install under `~/.claude/skills` is not auto-applied
as a Cursor workspace rule. Changing repo-ops itself: the checkout
[AGENTS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/AGENTS.md).

**Install and environment:** [HUMANS.md](HUMANS.md). **CLI flags:**
`repo-ops <subcommand> --help`. **Deep reference:** [docs/usage.md](docs/usage.md),
[docs/bulk-maintenance.md](docs/bulk-maintenance.md).

## Quick reference

**When a user requests `repo-ops apply` (or `plan`, `inventory`, etc.), run it
directly** — default roots apply when paths are omitted. Do not run
`which repo-ops`, read `src/` source, or open HUMANS.md to discover flags;
this file and `repo-ops <subcommand> --help` are enough.

| User request | Run immediately |
| -------------- | ----------------- |
| `/repo-ops apply` | `repo-ops apply --log-file apply.log` |
| `repo-ops plan` (preview drift) | `repo-ops plan --log-file plan.log` |
| Update deps in one checkout | `repo-ops plan --flat /path` then `repo-ops apply --flat /path` |

`plan`/`apply` run the full-coverage sweep by default: cross-range upgrades
for every ecosystem, per-package targeted bumps for every package manager,
GitHub Actions pin-to-SHA + digest refresh + cross-major, and container image
tag + digest bumps. No flags to add. Pass `--conservative` to stay in-range
instead, moving only what each manifest's own constraints already allow.

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

When no trailing `root_dir` arguments are passed, every subcommand except
`install-skill` and `doctor` scans
[`DEFAULT_ROOTS`](src/repo_ops_core/constants.py):

- `/usr/local/src`
- `~/Projects`
- `~/Documents`
- `~/Apps`

Pass explicit checkout paths to limit scope. Use `--flat` when a root is a
single repo (skip org/proj two-level walk).

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

# Submodule umbrellas: detection + children-first apply order are on by default
repo-ops plan --json --json-out plan.json /path/to/umbrella
repo-ops apply /path/to/umbrella
```

### Workflow

1. **`plan` before `apply`** — same flags and roots. Plan is read-only; exit
   `1` when actionable drift exists.
2. **Dirty worktrees** — skipped by default; use `--allow-dirty` only when
   intentional.
3. **Verify failures** — default `--on-verify-fail=revert` rolls back
   apply-artifact files (lockfiles and manifests); `--on-verify-fail=keep`
   leaves them for inspection.
4. **Commits** — fleet canon (`~/.claude/CLAUDE.md` § Git & GitHub); headless:
   `apply --commit=conventional` or `--commit=agent` (preview with
   `--commit-dry-run`).
5. **Bulk multi-repo playbook** — [docs/bulk-maintenance.md](docs/bulk-maintenance.md).

### Flags

Full flag reference: `repo-ops <subcommand> --help`. Discovery and security:
[docs/usage.md — Security and discovery](docs/usage.md#security-and-discovery).

### What NOT to do

- Do **not** explore the repo (`grep` flags in source, read `run.py` /
  `cli.py`, skim HUMANS) before routine `plan`/`apply` — use this file and
  `--help`.
- Do **not** run `which repo-ops` plus multiple `--help` calls when the user
  already named a subcommand and flag.
- Do **not** run `apply` without `plan` on the same flags/roots when drift is
  unknown **and** the user did not explicitly ask to apply.
- Do **not** use `--allow-dirty` to mask uncommitted work you did not intend
  to include.
- Do **not** duplicate CLI flag tables into README, AGENTS, or the bundled
  SKILL — `--help` is canonical.
- Do **not** push unless the user explicitly requests it.
- Do **not** assume verify failure means bad dependency bumps — check
  toolchain, env, and flaky tests first.
