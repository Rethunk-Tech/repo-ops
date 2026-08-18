---
name: repo-ops
description: >-
  Dependency inventory, repo status (ahead/behind/dirty), lockfile audit,
  plan/apply upgrades, Actions pins.
  Trigger: repo-ops, Dependabot, SBOM.
---

# repo-ops agent skill

Use the **`repo-ops`** CLI for dependency and Actions maintenance.

**Agent quick reference (invocations, DEFAULT_ROOTS, workflow, flags, do-nots):** [REFERENCE.md](REFERENCE.md) — bundled alongside this file; open it now. Changing repo-ops itself: checkout [AGENTS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/AGENTS.md). When the user names a command (e.g. `/repo-ops apply`), **run it directly** with default roots; do not run `which repo-ops`, read source, or probe `--help` first. `plan`/`apply` run the full-coverage sweep by default — no flags to remember. Pass `--conservative` only when a run must stay within existing manifest constraints.

**Install and environment:** [HUMANS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/HUMANS.md). **CLI flags (authoritative):** `repo-ops <subcommand> --help`. **Deep reference:** [docs/usage.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/usage.md), [docs/bulk-maintenance.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/bulk-maintenance.md).

## Bundled skill install

```bash
repo-ops install-skill    # ~/.cursor/skills/repo-ops and ~/.claude/skills/repo-ops
```

`install-skill` copies `SKILL.md` and `REFERENCE.md` (not the checkout `AGENTS.md`) with relative doc links rewritten to GitHub canonical URLs so links resolve from the skill install directory. The install copy is named `REFERENCE.md` so Cursor does not auto-apply it as a workspace rule.

Details: [Installation — Agent skill](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/installation.md#agent-skill).
