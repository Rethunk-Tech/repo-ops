---
name: repo-ops
description: >-
  Dependency inventory, repo status (ahead/behind/dirty), lockfile audit,
  plan/apply upgrades, Actions pins.
  Trigger: repo-ops, Dependabot, SBOM.
---

# repo-ops agent skill

Use the **`repo-ops`** CLI for dependency and Actions maintenance.

**Agent quick reference (invocations, DEFAULT_ROOTS, workflow, flags, do-nots):** [REFERENCE.md](REFERENCE.md) — bundled alongside this file; open it now. Changing repo-ops itself: checkout [AGENTS.md](https://github.com/Rethunk-Tech/repo-ops/blob/main/AGENTS.md).

## Bundled skill install

```bash
repo-ops install-skill    # ~/.cursor/skills/repo-ops and ~/.claude/skills/repo-ops
```

`install-skill` copies `SKILL.md` and `REFERENCE.md` (not the checkout `AGENTS.md`) with relative doc links rewritten to GitHub canonical URLs so links resolve from the skill install directory. The install copy is named `REFERENCE.md` so Cursor does not auto-apply it as a workspace rule.

Details: [Installation — Agent skill](https://github.com/Rethunk-Tech/repo-ops/blob/main/docs/installation.md#agent-skill).
