---
name: repo-ops
description: >-
  Dependency updates across local checkouts: bumps every ecosystem and
  GitHub Actions pins, verifies, commits. Trigger: repo-ops, dependency
  sweep, Dependabot.
---

# repo-ops

- Dependency updates: `repo-ops apply [path]`. It commits each verified repo and never pushes. The report path is printed last.
- No path sweeps every checkout under the default roots and skips dirty repos. A named path is processed even if dirty, and a failed verify there keeps the changes for inspection.
- Run it directly: no `--help`, no `plan` first.
- `repo-ops dependabot --close` closes Dependabot PRs the sweep superseded.
- Pushing stays yours: children before their umbrella parent.
