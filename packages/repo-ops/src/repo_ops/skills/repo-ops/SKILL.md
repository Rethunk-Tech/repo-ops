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

Default roots: [`DEFAULT_ROOTS`](https://github.com/Rethunk-Tech/repo-ops/blob/main/packages/repo-ops-core/src/repo_ops_core/constants.py). Pass checkout paths as trailing args to override. Common flags: `--flat`, `--filter ECO`, `--json`, `--json-out PATH` (with `--json`), `--submodules` (plan/apply JSON: `submodules: [{path, git_repo, parent}]`), `--submodule-order=children-first` (apply: child repos before parents), `--log-file`, `--strict-exit`, `--max-failures`, `--skip-actions`, `--skip-remediations`, `--no-verify`, `--on-verify-fail=revert|keep` (apply only; default `keep`).

## Workflow

1. **`plan` before `apply`** — show drift and remediations without writes (`plan` exits `1` when drift exists).
2. Omit roots to scan `DEFAULT_ROOTS`, or pass explicit paths / `--flat` for a single checkout.
3. `repo-ops <subcommand> --help` for full options; 0.2.x `repos-*` mapping in HUMANS migration section.

## Bulk apply playbook

For many repos under one parent root (e.g. `/usr/local/src/com.github`):

| Step | Action |
|------|--------|
| 1. Plan | `repo-ops plan /path/to/root --log-file plan.log` — or `plan --json --json-out plan.json` (stdout: path only) |
| 2. Dirty trees | Read skips for `dirty git worktree`; commit/stash per repo or add `--allow-dirty` when intentional |
| 3. Apply | `repo-ops apply /path/to/root --log-file apply.log` (footer: Summary roll-up, capped Failures, `failures:` / `exit_code:`). If piping: `set -o pipefail` then `2>&1 \| tee` |
| 4. Submodules | `plan --submodules` / `apply --submodule-order=children-first --submodules`; push children first, parent pointer commit last ([git-push-order](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md#submodule-umbrellas-git-push-order); footer **Submodule pointers**) |
| 5. Verify | Failed verify ≠ always bad deps — check env vars, flaky tests, local toolchain. Use `--on-verify-fail=revert` to roll back failed workspaces; JSON reports `verify_fail_disposition` (`FAILED_WITH_CHANGES` / `FAILED_CLEAN`) |

Full prose and related-issue links: HUMANS — [Bulk maintenance](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md#bulk-maintenance).

## Examples

```bash
repo-ops inventory --flat /path/to/my-repo
repo-ops plan --skip-actions --json --json-out plan.json /path/to/my-repo
repo-ops plan --submodules --json --json-out plan.json /path/to/umbrella
repo-ops apply --submodule-order=children-first --submodules /path/to/umbrella
repo-ops apply /path/to/my-repo
repo-ops apply --skip-actions /path/to/my-repo
repo-ops pushed --count-only /path/to/my-repo
```

## Bundled skill install

```bash
repo-ops install-skill    # ~/.cursor/skills/repo-ops and ~/.claude/skills/repo-ops
```

Details: HUMANS — Agent skill.
