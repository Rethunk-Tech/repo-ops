# TODO

Work that is decided but not built. `AGENTS.md` and `docs/` describe what
exists; this file is the only place that describes what does not. When an item
ships, delete its section.

Sections 1–8 are one plan, in landing order: each is one or more landable
commits, and later sections assume the earlier ones. The plan comes from an
audit (waste/debt/bypass lenses) measured against 362 unique recorded
invocations. Every decision below was taken by the operator; none is open.

## 4. apply's defaults are wrong, so every run passes the same flags

Unique-invocation counts: `--flat` 88/88 with an explicit root,
`--allow-dirty` 29/30 on an explicit root, `--on-verify-fail=keep` 15/15 on
an explicit root, `--commit=conventional` 50 (`none` 5, `agent` 0),
`--log-file` 105 (62 also redirected stdout, capturing twice), `--json-out`
without `--json` errored 16 times. `apply --help` is 1,126 words and was read
114 times.

Decided shape — the whole `apply` surface:

```
repo-ops apply [root ...]
  root           Omitted: fleet sweep of DEFAULT_ROOTS (dirty repos skipped, failed verify reverted).
                 Named: that checkout, even if dirty (only files apply touched are committed);
                 failed verify keeps the changes for inspection.
  --exclude PAT  Skip discovered repos whose path contains PAT (repeatable; config: exclude).
  --filter ECO   node|go|rust|py|deno|container|precommit|actions
  --no-commit    Leave changes uncommitted (default: one conventional commit per verified repo; never pushes).
  --no-verify    Skip the repo gate (implies --no-commit).
  --jobs N       Parallel workers (default min(16, cpus)).
  -v, --verbose  Echo subprocesses.
Every run writes $XDG_STATE_HOME/repo-ops/runs/<utc>-apply/{log,report.json}; both paths are the last line printed.
```

`plan` takes the same flags minus `--no-commit`/`--no-verify`. `dependabot`
takes `[root ...] --exclude --jobs -v --close`. These come from one shared
parent parser in `repo_ops_core/cli.py`; no subcommand declares a shared flag
itself.

Deleted: `--commit`, `--commit-dry-run`, `--commit-on-fail`, commit agent
mode (181 lines of Cursor SDK code — SDK pipelines do not live in product
repos), `--flat`, `--allow-dirty`, `--on-verify-fail`, `--log-file`, `--json`,
`--json-out`, `--compact`, and the 25 zero-use flags including `--pr`/
`--pr-base`/`--branch-prefix` (`pr.py`, 389 lines; pushes without per-push
approval), `--resume` (and its checkpoint), `--top`, `--only-outdated`,
`--strict-exit`, `--fail-fast`, `--skip-remediations`, `--verify-test`,
`--max-failures`, `--quiet`, `--log-level`, `--follow-symlinks`, `--manager`,
`--submodules`/`--no-submodules`/`--submodule-order` (children-first stays the
fixed behaviour), the remaining `--actions-*` knobs. Every "X requires Y"
check goes with its flags (13 pairs, 9 of them silent no-ops today).

Land as four commits: (a) commit default + agent mode removal; (b) roots-derived
dirty/verify-fail behaviour; (c) run dir replaces log/json flags, shared parent
parser; (d) remaining flag deletions.

### Read

- `src/repo_ops_core/cli.py:197-301` shared `add_*_argument` helpers;
  `:341-690` the upgrade parser.
- `src/repo_ops_upgrade/run.py:222-226,383-415` dirty snapshot and
  `rollback_on_veto`; `:896-897` json-out check; `:945` `tee_stdout_stderr`;
  the retry-failed-repos block (depends on revert — keep its condition true
  for fleet sweeps).
- `src/repo_ops_upgrade/discovery.py:485-500,555` (`--flat` is already a no-op
  for a root with its own `.git`, and ignored with >1 root).
- `src/repo_ops_upgrade/post_apply_commit.py` `plan_repo_commit` (dirty-repo
  scoping: only upgrade-scoped paths).
- `src/repo_ops_upgrade/report.py:441-457` duplicate of
  `repo_ops_core/filesystem.py:23-41` `write_json_atomic` — use the real one
  for `report.json`.
- `src/repo_ops_upgrade/pr.py`, `commit_agent*.py`, checkpoint code in
  `run.py:417-454`, `repo_ops_core/state.py` checkpoint functions.

### Traps

- **Named root, dirty repo, commit on.** The commit must stage only files
  apply changed (section 1's pathspec plus `upgrade_scoped_paths`); an
  operator's own edits in that checkout must stay uncommitted.
- **Exclude vs named roots.** Exclude applies only to discovered repos, never
  to a root named on the command line (today `run.py:307-318` would block the
  15+ explicit caldera runs once caldera is globally excluded).
- **Run dir must not grow forever.** Keep the newest N runs (e.g. 50) and
  delete older ones at run start; a fleet log is ~1 MB.
- **The umbrella commit and serial retry assume revert-on-failure.** Named
  roots keep changes on failure, so they must not be retried and must not be
  committed.

### Acceptance

- `repo-ops apply --help` shows exactly the block above (≤ 150 words).
- `repo-ops apply` (no roots) on a fleet with one dirty repo: dirty repo
  skipped, others committed, final line prints two existing paths.
- `repo-ops apply /path/dirty-checkout`: applied, only manifest/lockfile
  committed, the pre-existing edit still unstaged.
- A failing gate on a named root leaves the changes and no commit.
- Each deleted flag exits 2 as unrecognized. Full gate green.

## 5. Five config layers where one file with three keys is used

Sources today: CLI, 48 `REPO_OPS_*` env vars, global `config.toml`,
pyproject `[tool.repo-ops]`, `.repo-ops.toml`. Measured: 0 env vars set, 0
pyproject tables, 18 sidecars using only `verify` (15), `hold` (4),
`exclude` (1); the global file sets only `exclude`. Settings travel CLI →
`os.environ` (`_set_cli_env`) → ambient-snapshot diff → `resolve_setting`
(24 settings), 7 settings read raw `os.environ`, and `run.py:910` passes an
empty global config on fleet runs, so global keys other than exclude are
silently ignored. `resolve_bool_setting` is copied into 9 manager modules and
`process_env_cli_override` twice (the deno copy has drifted).

Decided: two files, three keys — `~/.config/repo-ops/config.toml` and
per-repo `.repo-ops.toml`, keys `exclude`, `[[hold]]`, `[verify]`. No env
vars, no pyproject layer. Timeouts and yarn concurrency become constants.
One frozen per-repo settings object is built once and passed down.
`exclude` resolves as the union of both files everywhere
(`merge_exclude_patterns`). Move the durable excludes (WineCountryVIP,
caldera: client repos outside operator orgs; 33 of 86 `--exclude` uses)
into the live global config in the same pass.

### Read

- `src/repo_ops_core/config.py` whole file (411 lines): `SETTINGS_TABLE`,
  `resolve_setting:339`, `resolve_bool_setting:392`, pyproject merge `:79-111`.
- `src/repo_ops_upgrade/run.py:228-236,905-918` `_set_cli_env`,
  `ambient_env`, `apply_config_to_env`.
- Raw env reads: `run.py:418`, `actions.py:892,1056`, `orchestration.py:103`,
  `state.py:67`, `yarn.py:103`, `hatch.py:37`; `REMEDIATE_GO_GENERATE` (3
  sites).
- Copies: `bun.py:79,90`, `pnpm.py:85,96`, `yarn.py:211,222`, `npm.py:59`,
  `rust.py:49`, `deno.py:45,69`, `go.py:151,167`.
- `src/repo_ops_core/exclude.py:21`; `src/repo_ops_dependabot/dependabot.py:1159-1166`
  (uses repo *or* global excludes — wrong).
- `~/.config/repo-ops/config.toml` (live operator config).

### Traps

- **`[verify]` is the one key doing real work** (15 sidecars): its schema and
  behaviour must not change; run each of the 18 sidecar repos through
  `repo-ops plan` before and after and diff the verify commands.
- **Config test rewrite.** 33 layering tests (642 lines) and most of
  `tests/test_config_e2e.py` (857 lines) test the deleted layers; replace
  with tests of the two-file merge, not ports of the old ones.
- **Tests patch module-level copies.** 21 patches target the per-module
  copies; they move to the settings object.

### Acceptance

- `rg 'REPO_OPS_' src` is empty; `rg 'tool.repo-ops' src` is empty.
- A global `exclude` and a repo `exclude` both apply on a fleet run and on
  `dependabot`.
- The 18 sidecar repos produce the same verify commands before/after.
- Full gate green.

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
