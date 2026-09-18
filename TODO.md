# TODO

Work that is decided but not built. `AGENTS.md` and `docs/` describe what
exists; this file is the only place that describes what does not. When an item
ships, delete its section.

Sections 1–8 are one plan, in landing order: each is one or more landable
commits, and later sections assume the earlier ones. The plan comes from an
audit (waste/debt/bypass lenses) measured against 362 unique recorded
invocations. Every decision below was taken by the operator; none is open.

## 8. ~9,600 words of docs and skill for one command

Word counts: `docs/usage.md` 6,197, `docs/bulk-maintenance.md` 668,
`docs/installation.md` 439, `docs/architecture.md` 333, `docs/testing.md`
332, `AGENTS.md` 316, `README.md` 247, `HUMANS.md` 214,
`docs/programmatic-imports.md` 228, installed skill `SKILL.md` 232 +
`REFERENCE.md` 449. `REFERENCE.md:38` is wrong (says `--commit` gates only
on an immutable install; it runs each repo's declared gate), which is why
agents hand-commit every dirtied repo. `AGENTS.md:22` and `REFERENCE.md:34`
prescribe plan-before-apply, a full rescan each time (plan 136 vs apply 253).

Decided, after sections 2–5 land (docs describe the final surface):

- Skill: one `SKILL.md` ≤ 120 words at top-level `skills/repo-ops/`, installed
  with `bunx skills add Rethunk-Tech/repo-ops`; no flag table. Core line:
  "dependency updates: `repo-ops apply [path]`; commits each verified repo,
  never pushes; report path printed last." Delete `REFERENCE.md`.
- `AGENTS.md`: contributor-only (layout, gate, release); no operator table.
  `CLAUDE.md` becomes a symlink to it (today a regular stub file).
- Merge `HUMANS.md` + `docs/installation.md` into `README.md`.
- `docs/usage.md` → ~1,200 words: what apply does per ecosystem, holds,
  verify, exit codes, config keys. Delete `docs/programmatic-imports.md`
  unless an importer exists (`rg` the fleet first).
- Drop the plan-before-apply rule.
- Outside the repo: `~/.claude/skills/modernize-project/dependencies.md`
  names the CLI; remove the stale synced skill copy under
  `~/.config/Code - Insiders/.../skills/repo-ops/`; delete the old installed
  `~/.claude/skills/repo-ops/REFERENCE.md` and `~/.cursor/skills/repo-ops/`.

### Acceptance

- `wc -w` over README, AGENTS, docs/*, skill ≤ 3,000.
- Every flag named in any doc exists in `repo-ops <cmd> --help`
  (script it: extract `--[a-z-]+` from docs, check against the parsers).
- `doc-audit` and `doc-scrub` clean.

## PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `repo-ops` wheel once the trusted-publisher claim
is configured. Open issue
[#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

### Read

- `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`);
  `docs/installation.md` ("After PyPI publish").

### Traps

- **`invalid-publisher` / "valid token, but no corresponding publisher"** for
  claim `sub: repo:Rethunk-Tech/repo-ops:environment:pypi` is a PyPI
  pending-publisher claim mismatch, not a CI defect; do not re-diagnose the
  workflow.

### Acceptance

- A `v*` tag run publishes to PyPI; `uv tool install repo-ops` from PyPI
  matches the GitHub Release version; #124 closed with the publish log.
