# Bulk Maintenance

Fleet sweeps and submodule umbrellas.

## Sweep

```bash
repo-ops apply
```

The report ends with a summary (workspaces ok, failed and skipped; dirty skips; verify failures; Actions bumps) and the log and `report.json` paths. Then:

- **Dirty skips** are repos a colleague or another session is working in. Leave them, or run `repo-ops apply <path>` on one once it is yours.
- **Verify failures** are not always bad bumps: a build that needs secrets, a flaky test, or a missing toolchain fails the same way. Read the log before holding a package. Fleet failures were already reverted.
- **Read the tree, not the roll-up.** A lockfile can move without the headline dependency changing.
- **Yarn `file:` links churn the lockfile.** A `file:../…` sibling rewrites resolution hashes with no version change.
- **Companion ranges.** `vitest` with `@vitest/coverage-v8`, or `playwright-core` with `@playwright/test`, must move together; a lockfile-only bump of one breaks verify.

## Submodule umbrellas

Umbrella repos (many Go or Node submodules) are applied children first. Each parent then gets one `chore(submodules)` commit staging exactly the gitlinks of children committed this run. A pointer still dirty afterwards (a child whose verify failed) is listed in the footer and in `submodule_pointer_hints`.

Push children first and the parent last, the same order as the `git-push-order` skill.

Every sweep bumps every child with a newer release, so an umbrella gets a pointer commit per sweep. That is deliberate: a single pass converges (measured on bastion-core), and a per-repo throttle or a release-age cooldown were both declined in favour of always-latest.
