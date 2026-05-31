---
name: repo-ops
description: >-
  Use repo-ops for dependency inventory, lockfile audits across monorepo roots,
  read-only upgrade planning, and apply-with-verify dependency and GitHub Actions
  pin updates in Rethunk-Tech checkouts. Trigger when the user asks to inventory
  or count dependencies, audit lockfiles, plan or apply dependency upgrades,
  update deps in a Rethunk checkout, bump GitHub Actions pins via plan/apply,
  scan outdated packages across sibling repos, or run proactive maintenance on
  local git roots under DEFAULT_ROOTS.
---

# repo-ops agent skill

Steer the user (or run yourself) through the **`repo-ops`** CLI installed via `uv tool install` (see [HUMANS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md) for install and environment). Do not duplicate full install docs here.

## When to use which subcommand

| Goal | Command |
|------|---------|
| Read-only footprint: direct deps, versions, on-disk sprawl | `repo-ops inventory` |
| Plan outdated deps, Actions pin drift, remediations (no writes) | `repo-ops plan` |
| Apply updates, Actions bumps, remediations, verify gates | `repo-ops apply` |
| Audit commits pushed to remotes in a time window | `repo-ops pushed` |

- **Plan** never mutates the tree; exit code `1` when actionable drift exists (CI-friendly).
- **Apply** performs updates; narrow scope with `--no-verify`, `--skip-remediations`, or `--skip-actions`.
- GitHub Actions workflow pins are included in plan/apply by default; use `--skip-actions` for dependency-only runs.

## Roots and filters

- Commands default to scanning [`DEFAULT_ROOTS`](https://github.com/Rethunk-Tech/repo-ops/blob/main/packages/repo-ops-core/src/repo_ops_core/constants.py) unless explicit root directories are passed as trailing arguments.
- `--flat` treats each root as a single project (skips org/proj two-level walk) on `inventory` and upgrade `plan`/`apply`.
- `--filter ECO` limits to one ecosystem (`node`, `go`, `rust`, `py`).
- `--json` on every subcommand for machine-readable output.

## Example invocations

```bash
# Footprint for one checkout
repo-ops inventory --flat /path/to/my-repo

# Read-only upgrade plan (deps + Actions unless skipped)
repo-ops plan /path/to/my-repo
repo-ops plan --skip-actions --json /path/to/my-repo

# Apply with default verify gates
repo-ops apply /path/to/my-repo

# Dependency-only apply
repo-ops apply --skip-actions /path/to/my-repo

# Pushed-commit audit (count only)
repo-ops pushed --count-only /path/to/my-repo
```

## Agent workflow hints

1. Prefer **`plan` before `apply`** so the user sees drift and remediations without writes.
2. For monorepos under Rethunk layout, omit roots to use `DEFAULT_ROOTS` or pass explicit checkout paths.
3. Use `repo-ops <subcommand> --help` for full flags; migration from 0.2.x `repos-*` scripts is documented in HUMANS.md.

## Install this skill globally

After installing the `repo-ops` tool:

```bash
repo-ops install-skill
```

Installs to `~/.cursor/skills/repo-ops/` (Cursor) and `~/.claude/skills/repo-ops/` (Claude Code). See HUMANS.md — Agent skill installation.
