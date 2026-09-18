# TODO

Work that is decided but not built. `AGENTS.md` and `docs/` describe what
exists; this file is the only place that describes what does not. When an item
ships, delete its section.

Sections 1–8 are one plan, in landing order: each is one or more landable
commits, and later sections assume the earlier ones. The plan comes from an
audit (waste/debt/bypass lenses) measured against 362 unique recorded
invocations. Every decision below was taken by the operator; none is open.

## 6. Helpers bypassed in the code that survives

After sections 2–5, these remain:

- 8 git calls bypass `repo_ops_core.git.run_git` with hardcoded timeouts:
  `actions.py:152`, `post_apply.py:68,81,147,175,253`,
  `remediations_core/remediations.py:69`. (`tags.py:41` `ls-remote` has no
  repo dir; leave it.)
- Hold ceiling check inlined ~11 times though `upgrade/holds.py:22`
  `target_within_hold` exists; `js/holds.py:46` `is_within_ceiling` is a
  one-line alias (7 sites). Move the helper to `repo_ops_core.holds`, delete
  the alias.
- Workspace-member matcher / `_under` in 3 copies (`js/workspaces.py:146`,
  `py_uv/uv.py:26`, `upgrade/discovery.py:178`); the yarn copy drifted
  (`rstrip('/*')`, no `/*` branch).
- TOML lock name→version parsing copied in `py/uv_lock.py:25`, `pdm.py:36`,
  `poetry.py:32`; use `lockfiles/parsers.py:257`.
- `verify.py:493` private `parse_requirements_lines` (drifted: no `.`→`-`
  normalisation); use `py/requirements.py:8`.
- `requirement.py:22` copies `version_parts`; `deno.py:278` copies
  `restore_workspace_snapshot`; `tags.py:114` inlines
  `holds.matching_sha_tags`; `actions.py:149` GitHub-remote parser drifted
  from `dependabot.py:188` (origin only, no `ssh://`) — keep the dependabot one.
- `actions.py:330-345` hand-writes its cache instead of `write_json_atomic`.

The other 24 production jscpd clones are contextual (per-manager defaults,
different data shapes, docstrings) and stay.

### Traps

- **Drifted copies change behaviour when unified.** The yarn matcher, the
  requirements normaliser and the remote parser each fix a real miss when
  consolidated; add one test per drift that fails on the old copy.

### Acceptance

- `rg -n 'subprocess.*\["git"' src` lists only `tags.py`.
- One commit per helper family; full gate green.

## 7. Tests hand-roll what conftest should provide

363 test clones (4,389 lines), 297 same-file: 261 local `def fake_run`,
161 inline `CompletedProcess` builds in 29 files, 12 local workspace builders,
5 private git-init helpers. `_fake_args` in `tests/test_run_wave2_wiring.py:21`
and `tests/test_actions_pool_isolation.py:23` already drifted from the parser
(`on_verify_fail="keep"` vs the parser's `revert`). About 270 lines only pin
flag registration or `SETTINGS_TABLE` rows (`test_policy_display.py:269-343`,
`test_config.py:89-200`, `test_cli.py:123-138,196-260`); they go with
sections 3–5.

Decided: add `fake_run` (call-recording) and `upgrade_args` (built from the
real parser, so it cannot drift) to `tests/conftest.py`; migrate local
copies file by file.

### Acceptance

- `rg -c 'def fake_run' tests` ≤ 5 (legitimately special ones).
- No test builds upgrade args by hand. Suite time does not regress.

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
