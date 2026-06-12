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

Use the **`repo-ops`** CLI for dependency and Actions maintenance.

**Agent quick reference (invocations, DEFAULT_ROOTS, workflow, flags, do-nots):** [AGENTS.md](AGENTS.md) — bundled alongside this file; open it now. ([canonical/latest on GitHub](https://github.com/Rethunk-Tech/repo-ops/blob/main/AGENTS.md)) When the user names a command (e.g. `/repo-ops apply --latest`), **run it directly** with default roots; do not run `which repo-ops`, read source, or probe `--help` first.

**Install and environment:** [HUMANS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md). **CLI flags (authoritative):** `repo-ops <subcommand> --help`. **Deep reference:** [docs/usage.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/usage.md), [docs/bulk-maintenance.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/bulk-maintenance.md).

## Bundled skill install

```bash
repo-ops install-skill    # ~/.cursor/skills/repo-ops and ~/.claude/skills/repo-ops
```

Details: [Installation — Agent skill](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/installation.md#agent-skill).
