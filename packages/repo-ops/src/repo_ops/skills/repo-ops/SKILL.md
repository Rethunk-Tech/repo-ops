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

Use the **`repo-ops`** CLI for dependency and Actions maintenance. **Install, flags, Go modes, verification:** [HUMANS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md) (editable install: `uv tool install -e packages/repo-ops` from a clone). **Repo layout for agents:** [AGENTS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/AGENTS.md).

## Subcommands

| Goal | Command |
|------|---------|
| Footprint (direct deps, versions, sprawl) | `repo-ops inventory` |
| Plan drift (no writes) | `repo-ops plan` |
| Apply updates + verify | `repo-ops apply` |
| Pushed-commit audit | `repo-ops pushed` |

Default roots: [`DEFAULT_ROOTS`](https://github.com/Rethunk-Tech/repo-ops/blob/main/packages/repo-ops-core/src/repo_ops_core/constants.py). Pass checkout paths as trailing args to override. Common flags: `--flat`, `--filter ECO`, `--json`, `--skip-actions`, `--skip-remediations`, `--no-verify`.

## Workflow

1. **`plan` before `apply`** — show drift and remediations without writes (`plan` exits `1` when drift exists).
2. Omit roots to scan `DEFAULT_ROOTS`, or pass explicit paths / `--flat` for a single checkout.
3. `repo-ops <subcommand> --help` for full options; 0.2.x `repos-*` mapping in HUMANS migration section.

## Bulk apply playbook

For many repos under one parent root (e.g. `/usr/local/src/com.github`):

| Step | Action |
|------|--------|
| 1. Plan | `repo-ops plan /path/to/root 2>plan.stderr \| tee plan.log` — or `plan --json … > plan.json 2>plan.stderr` |
| 2. Dirty trees | Read skips for `dirty git worktree`; commit/stash per repo or add `--allow-dirty` when intentional |
| 3. Apply | `set -o pipefail` then `repo-ops apply /path/to/root 2>&1 \| tee apply.log` (`--log-file` is [#10](https://github.com/Rethunk-Tech/repo-ops/issues/10), not shipped yet) |
| 4. Submodules | Apply/push **children first**, parent submodule pointer commit **last** (git-push-order pattern; [#13](https://github.com/Rethunk-Tech/repo-ops/issues/13)) |
| 5. Verify | Failed verify ≠ always bad deps — check env vars, flaky tests, local toolchain before reverting ([#9](https://github.com/Rethunk-Tech/repo-ops/issues/9)) |

Full prose and related-issue links: HUMANS — [Bulk maintenance](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md#bulk-maintenance).

## Examples

```bash
repo-ops inventory --flat /path/to/my-repo
repo-ops plan --skip-actions --json /path/to/my-repo
repo-ops apply /path/to/my-repo
repo-ops apply --skip-actions /path/to/my-repo
repo-ops pushed --count-only /path/to/my-repo
```

## Bundled skill install

```bash
repo-ops install-skill    # ~/.cursor/skills/repo-ops and ~/.claude/skills/repo-ops
```

Details: HUMANS — Agent skill.
