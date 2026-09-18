# Usage

Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes, Go modules, and Yarn isolated plan for the `repo-ops` CLI.

## Subcommands

Subcommands (entry point in `src/repo_ops`):

| Subcommand | Purpose |
| ------------ | --------- |
| `plan` | Outdated deps, Actions pin drift, remediations (no writes) |
| `apply` | Apply updates, Actions bumps, remediations, verify gates |
| `dependabot` | List/close Dependabot PRs superseded by local manifests |

Default scan roots: [`DEFAULT_ROOTS`](../src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. Discovery flags (`--flat`, `--follow-symlinks`, actions-only repos): [Security and discovery](#security-and-discovery).

Flags shared across subcommands (`repo-ops <subcommand> --help` is authoritative):

| Flag | Effect |
| ------ | -------- |
| `--json` | JSON instead of human output. Every subcommand. Reports carry a top-level `summary` mirroring the human footer, and discovery notes in `skipped`/`warnings`. |
| `--json-out PATH` | Write the report to `PATH` atomically; stdout prints only the path. `plan`, `apply`. |
| `--filter ECO` | One ecosystem: `node`, `go`, `rust`, `py`, `container`, `precommit`, `actions`, `deno`. |
| `--manager MANAGER` | `plan`/`apply` only. One package manager: `bun`, `npm`, `pnpm`, `yarn`, `uv`, `poetry`, `pip-tools`, `hatch`, `pdm`, `pipenv`, `deno`, `go`, `rust`, `container`, `precommit`. |
| `--log-level LEVEL` | `debug`/`info`/`warning`/`error`. `--verbose`/`-v` echoes subprocesses and implies `debug`. |
| `--log-file PATH` | `plan`/`apply` transcript (stdout + stderr) to `PATH`, no shell pipe needed. |
| `--no-submodules` | Skip the submodule scan. Detection is on by default and adds `submodules[]` to plan/apply JSON. |
| `--submodule-order` | `children-first` (default when `.gitmodules` exists) applies child repos before parent umbrellas; `discovery` restores plain scan order. |

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly). Node **check** runs `corepack prepare` (via `check_yarn_snapshot`) so the correct Yarn version is activated in the temporary workspace — plan has a corepack side-effect and may access the network.
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope. On verify failure, `--on-verify-fail=revert` (default) restores every file the workspace's apply and remediations changed (edits present before apply are kept) and reinstalls from the restored lockfile; with `--commit`, the failure also reverts the repo's passing workspaces, whose commit it vetoes. This is the default because the cross-range bulk upgrade a sweep performs has no per-package rollback of its own; `--on-verify-fail=keep` leaves the worktree as-is for inspection. Human and JSON workspace rows include `verify_fail_disposition` (`FAILED_WITH_CHANGES` vs `FAILED_CLEAN`) when verify fails.
- **Declared gates first:** where the [`gate`](https://github.com/Rethunk-Tech/rethunk-gate-cli) CLI is installed and detects gates for a workspace, apply verification runs `gate run <detected names>` — the project's Makefile targets, turbo tasks and `.gate.toml` entries — instead of repo-ops' own guess at them. Ecosystem lockfile checks (`bun install --frozen-lockfile`, `go mod verify`, `uv sync --locked`) still run first, and an explicit `REPO_OPS_NODE_VERIFY_SCRIPT` or `verify.script` still wins over both. Without `gate`, or where it detects nothing, verification degrades to the built-in per-ecosystem gates below.
- **Verification tests:** `--verify-test` runs the additional Go and Rust test gates during apply verification. Go tests run with the Go verify gates, and Rust runs `cargo test` after `cargo check`; the default remains off. It also drops the declared `test` gate from a delegated `gate run`.
- Actions pins included by default; `--skip-actions` for dependency-only runs.
- **Cross-major Actions bumps:** By default a newer major upstream (e.g. `actions/checkout@v4` → `v6`) is applied: tag-style `major` rewrites to the floating `@v{max}` (only when a bare `v{max}` tag exists upstream — same safety check as normalize), tag-style `exact` rewrites to the highest `@v{max}.M.P`. When the newest major cannot be safely targeted, it falls back to normalize/report-only. `--actions-upgrade-mode=normalize` keeps Actions within-major only (report the newer major instead of applying it) without disabling digest refresh or pin-to-SHA for those same Actions, and without affecting any other ecosystem's sweep. `--conservative` turns off cross-major bumps, digest refresh, and pin-to-SHA together. For SHA-pinned actions, digest refresh re-pins to the newest major's commit SHA (with comment update) by default — see SHA digest refresh below.
- **SHA pins (default: preserve):** By default, `uses: owner/repo@<sha>` pins are never unpinned to a floating tag on apply (supply-chain hardening) — this default holds regardless of `--conservative`. Opt in to tag resolution with `--actions-unpin-sha`. When unpinning: (1) trailing `# vN[.N.N]` comment → use that major; (2) tags API SHA→tag lookup → highest semver tag for that commit. If neither resolves, the SHA is reported but left untouched. `--actions-tag-style=major` (default) → `@vN`; `exact` → highest `@vN.M.P` within the resolved major (falls back to `@vN`).
- **SHA digest refresh (keep pinned):** By default, a `uses: owner/repo@<sha>  # vN` action pinned **by digest** has its SHA advanced to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` comment — superseding Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. This also **crosses major boundaries** by default: re-pins to the newest major's commit SHA and updates the `# vN` comment to the new major (e.g. `# v6` → `# v7`) — superseding Dependabot's cross-major SHA-pin PRs such as `actions/checkout` v6→v7 or `sigstore/cosign-installer` v3→v4. Falls back to within-major refresh when the newest major has no exact `vN.M.P` tag. The major is resolved from the `# vN` comment, else the tags API. No effect with `--actions-unpin-sha` (that resolves the SHA to a tag instead); a no-op when the major can't be resolved or the pin is already the newest digest. `--conservative` turns this off entirely (SHA pins stay report-only).
- **Floating (never-SHA-pinned) Actions:** By default, a floating tag pin (`@vN`, `@vN.M.P`) is converted to the commit SHA of the highest `vN.M.P` release in its resolved major, adding or replacing a `# vN.M.P` comment; crosses major the same way digest refresh does. `--conservative` leaves floating tags untouched.
- **Pre-commit SHA refresh:** By default, resolvable SHA-pinned `rev:` entries stay pinned by digest while advancing to the newest release within the tagged major for the same hook repository. `--conservative` leaves SHA pins report only; unresolvable pins remain unchanged either way.
- **Submodule SHA refresh:** By default, SHA-only submodule HEADs stay pinned by commit while advancing to the newest resolvable release within the tagged major for the same `.gitmodules` URL. `--conservative` leaves SHA-only pointers report only; unresolvable pins remain unchanged either way.
- **Full coverage is the default.** `plan`/`apply` cross ranges for every ecosystem, targeted-bump every package manager, and refresh/pin Actions, container, pre-commit, and submodule SHAs — all without any flag. `--conservative` is the single opt-out: stay in-range, only move what each manifest's own constraints already allow.
- **Human-only filtering (`--only-outdated`):** Human-readable output omits clean workspaces by default. `--no-only-outdated` lists every workspace scanned. JSON output remains complete and unfiltered either way — `--only-outdated` never removes rows from JSON reports.
- **Remediations:** `--skip-remediations` accepts the current remediation IDs below. Remediations are enabled by default unless marked off.

| ID | Ecosystem | Trigger | Default |
| ---- | --------- | ------- | ------- |
| `biome_schema` | Node | Biome schema remediation | on |
| `playwright_browsers` | Node | Playwright browser remediation | on |
| `codegen` | Node | Configured code-generation command | on |
| `package_manager_pin` | Node | `packageManager` pin remediation | on |
| `manifest_format` | Node | `biome format --write` on the `package.json` files apply rewrote, when a Biome config governs them | on |
| `gofmt` | Go | `gofmt` remediation | on |
| `go_generate` | Go | `go generate` remediation | off |
| `ruff_fix` | Python | Ruff autofix remediation | on |
| `cargo_fix` | Rust | `cargo fix` remediation | on |
| `deno_fix` | Deno | Deno lint-fix remediation | on |

`go_generate` remains off unless `REPO_OPS_REMEDIATE_GO_GENERATE=1` is set.

- **Bun remediations:** execute installed package binaries via `bunx` (the
  `pm_exec_prefix("bun")` command prefix), not `bun run`.

- Parallelism: `--jobs` must be a positive integer (default `8`).
- **Staleness scoring:** every run persists per-repo behind-latest history (`$XDG_CACHE_HOME/repo-ops/state.json`) and reports `staleness_score` (behind-latest count × days since the repo first fell behind) and `new_behind_since_last_run` in JSON, plus a top-3 staleness footer line when any score is nonzero. Disable all state persistence with `REPO_OPS_NO_STATE=1`.

### Security and discovery

- **Scan roots:** `resolve_scan_roots` expands paths, requires existing directories, and rejects tokens that look like `find(1)` predicates (for example `-name`).
- **Symlinks:** `find` does not follow symlinks by default. Pass `--follow-symlinks` on `plan` or `apply` when symlinked `.git` dirs must be traversed (`find -L`).
- **Actions-only repos:** Git repos with workflow files but no dependency workspaces still appear in plan/apply (Actions pin audit only).
- **`--flat`:** On `plan`/`apply`, treats each root as a single project rather than discovering the checkouts beneath it.
- **Node `packageManager`:** Only `npm`, `pnpm`, `yarn`, and `bun` with `name@version` form are accepted; path-like or unknown names are skipped with diagnostics.
- **`--exclude PATTERN`:** Repeatable on `plan`, `apply`, and `dependabot`. A repository is skipped when a pattern substring- or fnmatch-matches its label or absolute path. Unlike every other setting, `--exclude` does not override the config layers — CLI, per-repo `exclude`, and global `exclude` are unioned, so an ad-hoc flag cannot silently pull a permanently-excluded repo back into a sweep.

When JSON is enabled, audit reports excluded repositories in
`summary.excluded`; dependabot uses the same canonical `summary.excluded` key.

### Ecosystem upgrade modes (plan and apply)

Full coverage is the default: every ecosystem below crosses ranges and moves
`behind_latest` gaps without any flag. `--conservative` is the single
opt-out, per ecosystem behavior described in its own column.

Node monorepos cover every workspace member (root `package.json` `workspaces`, or
`pnpm-workspace.yaml` `packages`), not just the root manifest. npm, pnpm, and bun
check and bump each member from its own directory, so a bump saves into the member's
manifest, exact pins stay exact, and workspace-scoped holds match the member path.
Yarn Berry's `yarn up` is already project-wide. Snapshots and the verify-fail revert
include member manifests.

A `"private": true` member's `peerDependencies` follow the version that same manifest
depends on: an exact peer pin moves to it and a `^`/`~` floor is raised to it, never
lowered, then the lockfile is reinstalled. Holds apply at the member's path. Published
members' peer ranges, and peers the member does not also depend on, are left alone.

| Ecosystem | Default (full sweep) | `--conservative` |
| ----------- | ----------------------- | ------------------- |
| **npm** | `npm update`, then a reversible per-package targeted bump (`npm install <pkg>@latest`) for any dep left `behind_latest` after the update | `npm update` only (semver ranges in manifests); gaps stay reported as `behind_latest` |
| **pnpm** | `pnpm update -r --latest` (cross-range), plus a reversible per-package targeted bump for behind-latest fallback, including `catalog:` / `catalogs:` entries in `pnpm-workspace.yaml` | `pnpm update -r` within existing ranges only |
| **yarn** | `yarn up -R '*'` (Berry, cross-range), plus a reversible per-package targeted bump (`yarn up <pkg>@latest`) for any direct dep yarn's resolver still kept behind its newest published version, kept only if the workspace still installs and the version actually advances | `yarn up` — in-range bumps only, in isolated plan snapshots |
| **bun** | `bun update` including out-of-range "latest" packages identified via `bun outdated`, plus a reversible per-package targeted bump (`bun add <pkg>@<version>`) for any remaining gap, kept only if the workspace still installs and the version actually advances | `bun update` within existing ranges only |
| **go** | `REPO_OPS_GO_UPGRADE_MODE=explicit` (all `list -u` updates) unless `--go-upgrade-mode` overrides it | `REPO_OPS_GO_UPGRADE_MODE=patch` (same-minor-line patches only) unless `--go-upgrade-mode` overrides it |
| **rust** | Direct Cargo.toml constraint bumps for crates.io deps (including `[workspace.dependencies]`) outside the current req, on top of `cargo update`. Rust major upgrades stay out of scope for repo-ops; edit manifests by hand | `cargo update --dry-run` / `cargo update` — within existing semver constraints in `Cargo.lock` only |
| **python** | Manager-specific; see the poetry/pdm/pipenv/pip-tools rows below. `uv` always stays constraint-respecting (`uv lock --upgrade`) regardless of `--conservative` | Same as default for `uv`; poetry/pdm/pipenv/pip-tools stay in-range |
| **poetry** | `poetry update`, plus a reversible per-package `poetry add pkg@^X` when the lock is behind a published version the current range can't take; skipped with a WARN when `poetry` is not on PATH | `poetry update --dry-run` / `poetry update` — in-range only |
| **pdm** | `pdm update`, plus a reversible per-package `pdm add pkg@^X`; skipped with a WARN when `pdm` is not on PATH | `pdm update --dry-run` / `pdm update` — in-range only |
| **pipenv** | venv `pip list --outdated --format=json` (via `pipenv --venv`, read-only; never creates a venv) / `pipenv update`, plus a reversible per-package `pipenv install` when an existing venv is detected; skipped with a WARN when `pipenv` is not on PATH or the venv is not initialised | Same read-only outdated check / `pipenv update`, no targeted bump |
| **pip-tools** | `uv pip compile` or `pip-compile` from `requirements.in`, plus rewriting `requirements.in` when its constraint blocks the latest published version, then recompiling reversibly | Compile from `requirements.in` within existing constraints only |
| **deno** | `deno outdated --update --latest` (Deno ≥ 2.x, cross-range), plus a reversible per-package fallback via selective `deno update <pkg>` for any remaining gap | `deno outdated --update` — in-range only; skipped with a WARN when `deno` is not on PATH |
| **pre-commit** | Cross-major hook rev bumps against latest upstream tags, plus resolvable SHA pins refreshed within their tagged major | Within-major rev bumps only; SHA pins stay report-only |
| **GitHub Actions** | `--actions-upgrade-mode=latest`: cross-major bumps applied; SHA pins get digest refresh (within-major advance, or cross-major re-pin with comment update); floating tag pins get converted to SHA pins. `--actions-unpin-sha` (independent of the sweep default) resolves SHA pins to tags instead; `--actions-tag-style=exact` picks the highest `@vN.M.P` when unpinning. Org allowed-actions policy checking runs automatically whenever `gh` is authenticated (probed via `gh auth token`); `--actions-org-policy` forces it on regardless. `--actions-policy-level=report\|warn\|block` (default `report`) controls whether violating pin rewrites are annotated only, warned, or blocked (`policy-blocked`) | `--actions-upgrade-mode=normalize`: exact tag pins normalize to the floating major (`@vN`); a newer major upstream is reported only; SHA pins stay report-only (preserve digest); floating tags are left untouched |
| **container** | Checks `FROM` / `image:` references in Dockerfiles and Compose files; cross-major tag bumps applied; SHA-pinned images get digest refresh to the latest digest for the same tag (keeps the pin hardened, advances to current content) | Within-major tag bumps only; SHA-pinned images stay report-only |

#### Hatch and `--migrate-to-uv-lock`

| Topic | Behavior |
| ------ | --------- |
| Lock creation | Hatch workspaces without `uv.lock` get one on apply by default (`--migrate-to-uv-lock`, `REPO_OPS_HATCH_MIGRATE_UV_LOCK=1`); `--no-migrate-to-uv-lock` skips them permanently |
| Verify without lock | Skips `uv sync --locked`; runs hatch fast gates only (`check`/`typecheck`, ruff, mypy, pyrefly when configured) |
| Pyrefly scope | `[tool.pyrefly]` `project-excludes` / `project-includes` passed as CLI flags |
| Fast gates | **uv**/**hatch** via `uv run`; **poetry**/**pdm**/**pipenv** via their `run` subcommands |
| Missing binaries | Fail closed with a clear reason |

### Go modules

`REPO_OPS_GO_UPGRADE_MODE` aligns plan and apply. Its own default is `patch`
(what `--conservative` falls back to); the default full sweep sets it to
`explicit` automatically. `--go-upgrade-mode` always overrides either default:

| Mode | Plan | Apply |
| ------ | ------ | ------- |
| `patch` (`--conservative` default) | `go list -m -u -json`, same minor-line patches only | `go get -u=patch all`, `go mod tidy` |
| `explicit` (full-sweep default) | all `list -u` updates | batched `go get path@version`, tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u -t all`, tidy |

### Yarn isolated plan

Isolated snapshots copy `.yarn/releases/` (Berry) and `file:../…` siblings from `package.json`; skip `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in plan JSON `error`. Upgrade command policy: table above (`REPO_OPS_YARN_UPDATE_LATEST`).

#### Targeted bump fallback

`yarn up -R '*'` keeps peer/group-constrained direct deps below their newest release; `behind_latest` reports the gap. By default repo-ops closes it:

1. Snapshot lockfile/manifests.
2. Run targeted `yarn up <pkg>@latest` + `yarn install`.
3. Keep only when install succeeds **and** the resolved version advances.

Failures (install break, ghost bump) restore the snapshot. Successful bumps appear in `plan`/`apply`. Same shape for every package manager; `--conservative` disables the fallback.

| Case | Behavior |
| ------ | ---------- |
| Manifest pin (`resolutions`/`overrides`/`pnpm.overrides`) | Released before retry when no [package hold](#package-holds--capping-a-packages-version-temporarily) covers the package; reverted on failure |
| Active hold | Pin kept; bump only up to hold ceiling |
| Ghost-bump guard (yarn/bun) | Blocked targets handed to this retry; raises only when `--conservative` leaves no fallback |
| Deno | Selective `deno update <pkg>` with lock/import-map snapshot restore |

### Submodule tag-advance

| Setting | Behavior |
| --------- | ---------- |
| Default | `--submodules` on: semver tag advances detected per submodule |
| Cross-major | Surfaced and applied by default; SHA-only HEADs refreshed when upstream tags resolve |
| `--conservative` | Within-major only; SHA-only HEADs report-only |
| Apply order | Submodule pointers updated before parent commits (`--submodule-order=children-first`; `discovery` restores scan order) |
| Opt out | `--no-submodules` skips the scan |

### Apply resume

Use `--resume` or set `REPO_OPS_RESUME=1` on `apply` to resume an interrupted bulk run without re-applying repos that already succeeded. Resume requires `--commit=conventional` or `--commit=agent`; `REPO_OPS_RESUME=1` alone writes nothing without `--commit`. After each repo's apply and verify succeed and its requested commit completes, `apply` writes the post-commit git HEAD SHA to a checkpoint file in the XDG cache directory. On re-run with resume enabled, repos whose current HEAD matches the checkpointed SHA are skipped as `resume: already applied`. If the HEAD has changed since the checkpoint (e.g. a commit landed in the meantime), the checkpoint entry is ignored and the repo is processed normally. To clear a stale checkpoint, delete `~/.cache/repo-ops/apply-checkpoint.json`.

### Branch / PR workflow (apply)

After a successful apply+verify pass with commits, opt into branch-and-PR mode instead of leaving changes on the current branch:

- **`--pr`** — create a branch, push, and open a PR via `gh` for each repo that received a commit (requires authenticated `gh`). Default: off (in-place commit only, no push).
- **`--pr-base BRANCH`** — base branch for opened PRs (default: repo default branch from `gh`).
- **`--branch-prefix PREFIX`** — branch name prefix (default: `repo-ops/deps`; resulting name `{prefix}/{repo-slug}`).

Idempotent when an open PR already exists for the same branch. Combine with `--commit=conventional` or `--commit=agent` so there are commits to push. See [Bulk maintenance](bulk-maintenance.md) for MCP commit guidance.

### `dependabot` — Dependabot PR sweep

`repo-ops dependabot [root_dir ...]` discovers repos with a `github.com` remote,
lists open Dependabot PRs via `gh pr list --author app/dependabot`, and checks
whether the bump is already satisfied by local lockfiles or manifests.

**Default (dry-run):** prints the "would-close" list with supersession status.
**`--close`:** closes superseded PRs via `gh pr close <n> --comment "Superseded..."`.

Precision caveats:

- **High-confidence:** lockfile parsers resolve npm/yarn/pnpm/bun (text
  `bun.lock`, and binary `bun.lockb` when `bun` can dump it), `uv.lock`,
  `poetry.lock`, `pdm.lock`, and `Pipfile.lock` versions; go.mod `require`
  blocks and Cargo.lock `[[package]]` entries are parsed structurally. These
  Python lockfile matches can qualify Dependabot PRs for `--close`; grouped
  PRs still require every row to be high-confidence and satisfied. Missing
  `bun` leaves `bun.lockb` low-confidence / unresolved.
- **Low-confidence:** text-search fallback on lockfile `name@version` strings when
  parsers cannot resolve a package. Low-confidence matches are **never** auto-closed.
- **Grouped PRs:** closed only when **all** rows are high-confidence satisfied.
- Title parsing covers `Bump X from A to B` / `Bumps X from A to B`; non-standard
  Dependabot titles are reported but never auto-closed.
- `gh` must be authenticated and on PATH; repos where `gh` fails are reported
  with an error and skipped.

`--json` emits per-repo PR lists with `superseded`, `current_ver`, `low_confidence`, and `closed` fields.

## Settings reference

One layered lookup per setting: **CLI flag** → **env var** → **config file** → **default**.

Every setting's env var is `REPO_OPS_<NAME>` and its config key is `<name>`, so
only the flag and the default are worth tabulating. The two exceptions are
`jobs` and `exclude_patterns`, which have no env var (arrays and run-wide values
do not serialize to one). [`SETTINGS_TABLE`](../src/repo_ops_core/config.py) is
canonical if this page and the code ever disagree.

Config files are TOML, read via stdlib `tomllib`:

- **Global:** `~/.config/repo-ops/config.toml` (or `$XDG_CONFIG_HOME/...`)
- **Per-repo:** `[tool.repo-ops]` in the repo root `pyproject.toml`, or flat keys
  in `.repo-ops.toml` for repositories without a suitable pyproject
- **Per-workspace:** the same two files in a workspace directory below the repo
  root. Only their `[verify]` table is read there, and it wins over the repo's,
  so `skip = true` opts out one workspace (e.g. a fixture broken on purpose)

**Scope.** Settings resolve from *each checkout's* config during multi-root
`plan`/`apply` — the launch checkout's config is not inherited by the fleet. The
exceptions are `jobs` and `yarn_max_concurrency`, which apply to the whole run.
CLI flags and caller-provided env vars outrank config everywhere.

**The `--conservative` family.** 22 settings share one flag and one
default of `1`: the full sweep is on unless `--conservative` turns it off. They
are the per-ecosystem `*_update_latest`, `*_targeted_bump`, and `*_refresh_sha`
keys, plus `actions_pin_sha`. Set one to `0` in config to opt a single ecosystem
out while the rest of the sweep stays on.

| Setting | Flag | Default |
| --------- | ------ | --------- |
| `actions_lint_level` | `--actions-lint-level` | `block` |
| `actions_org_policy` | `--actions-org-policy` | `0` |
| `actions_policy_level` | `--actions-policy-level` | `report` |
| `commit_agent_model` | `(none)` | `composer-2.5` |
| `commit_agent_provider` | `(none)` | `cursor` |
| `exclude_patterns` | `--exclude` | `[]` |
| `find_timeout` | `(none)` | `60` |
| `git_timeout` | `(none)` | `10` |
| `go_directive` | `(none)` | `(none)` |
| `go_toolchain` | `(none)` | `(none)` |
| `go_upgrade_mode` | `--go-upgrade-mode` | `patch` |
| `go_work_sync` | `(none)` | `0` |
| `hatch_migrate_uv_lock` | `--migrate-to-uv-lock` | `1` |
| `jobs` | `--jobs` | `min(16, cpu_count)` |
| `no_lint_cache` | `(none)` | `0` |
| `no_progress` | `(none)` | `0` |
| `no_state` | `(none)` | `0` |
| `node_verify_script` | `(none)` | `(none)` |
| `resume` | `(none)` | `0` |
| `timeout_apply` | `(none)` | `60` |
| `timeout_check` | `(none)` | `60` |
| `timeout_verify` | `(none)` | `60` |
| `yarn_max_concurrency` | `(none)` | `4` |

## Package holds — capping a package's version temporarily

`exclude_patterns` above excludes whole repos; a **hold** does the same job
at package granularity — cap one package (e.g. `typescript`) at a maximum
version, optionally until an expiry date, without disabling the default full
sweep for every other package in the repo.

Holds are config-file only (no CLI flag or env var — arrays don't serialize
to env, same reasoning as `exclude_patterns`), read from the same layered
files as every other setting: **global**
(`~/.config/repo-ops/config.toml`) and **per-repo**
(`[tool.repo-ops]` table in `pyproject.toml`, or a flat `.repo-ops.toml`
sidecar for non-Python repos), via a `[[hold]]` array-of-tables:

```toml
[[hold]]
package = "typescript"       # exact name or fnmatch glob, e.g. "@types/*"
max_version = "6"            # ceiling: "6" blocks major 7+, "5.9" blocks >5.9, "5.9.2" is exact
until = "2026-09-01"         # optional ISO date; omit = indefinite hold
ecosystem = "node"           # optional filter: an ecosystem (node|go|rust|py|deno|container|
                              # precommit|actions|submodule — the same set --filter accepts, plus
                              # submodule) or a manager (npm|yarn|pnpm|bun|cargo|uv|pdm|pipenv|
                              # pip-tools|poetry|hatch); aliases: js/javascript -> node, python -> py
workspace = "packages/legacy-app"  # optional fnmatch glob, relative to repo root; omit = whole repo
reason = "TS7 breaking changes; wait for ecosystem to catch up"   # free text, documentation only
```

**Precedence:** workspace-scoped beats repo-wide beats global; a per-repo
rule can also fully override a global rule for the same package/workspace
(repo config wins at equal specificity, same as every other layered
setting). A rule with `until` in the past is treated as absent — no
auto-cleanup, no file rewriting (config files stay read-only per the
Settings reference above); a one-time note is printed to stderr the first
time an expired hold is skipped.

**Visibility:** a package a hold declined to bump lands in the same
behind-latest bucket as any other outdated package, but is named as such —
its behind-latest line reads `held <= <max_version>` (plus `, until <date>`
when the rule has one), the per-workspace `behind-latest:` header adds a
`N held` count, its JSON `behind_latest` entry carries `"held": "<max_version>"`,
and the plan/apply `summary` (JSON and human footer) carries a `packages_held`
count alongside `packages_behind_latest`. An entry from a monorepo member names it
in JSON `member`, and its hold is resolved at that member's path.

Example — hold TypeScript fleet-wide at 6.x, but let one already-migrated
workspace bump freely:

```toml
[[hold]]
package = "typescript"
max_version = "6"
until = "2026-09-01"

[[hold]]
package = "typescript"
max_version = "99"          # effectively unbounded — the workspace override
workspace = "packages/modern-app"
```

### Expo SDK alignment

A JS workspace (root or member) whose `package.json` depends on `expo` gets holds
without any config, read from the installed `expo/bundledNativeModules.json`
(resolved from `node_modules` in the workspace, then the repo root) — the data
`expo install --check`/`--fix` and expo-doctor validate against offline:

- Each declared dependency or devDependency that file lists is held at its bundled
  spec: `19.2.3` caps exactly, `~2.32.0` at `2.32`, `^15.0.2` at `15`. Apply
  writes the bundled spec itself (`~` kept, exact stays exact), as `expo install` does.
- A listed package installed outside its spec is proposed as a `plan` change to the
  spec's floor in either direction (bun), e.g. `react-native 0.86.2 → 0.86.3`.
- `expo` is held at its installed major with spec `~<installed>`: patches move, an
  SDK upgrade stays a manual, reviewed change. `expo-*` packages the file lists
  follow their bundled spec; unlisted ones are ordinary dependencies.
- Names in the app's `expo.install.exclude` are skipped (a versioned entry counts
  when it names the bundled spec).
- The Expo hold wins over a `[[hold]]` rule for the same package. Rows it governs
  carry the reason in the human report (`held <= 2.32, expo SDK 57 bundles ~2.32.0`,
  and on change rows `(expo SDK 57 bundles 0.86.3)`) and in JSON `hold_reason`.

Online, the Expo CLI also overlays api.expo.dev's per-SDK versions on the file, so a
package that list names but the file does not (e.g. `@types/react`) still needs a
`[[hold]]`.

**Enforcement scope:** JS (npm, yarn, pnpm, bun), Deno, Go, Rust/Cargo, the
Python managers (uv, hatch, pdm, pipenv, poetry, pip-tools), pre-commit, GitHub
Actions, containers, and submodules.

**How it's enforced.** One rule everywhere: bump targets above the ceiling are
filtered out of `plan`, and refs *already* above it (a hold added after the
fact) are surfaced as actionable plan entries and clamped on `apply` to the
highest available version within the ceiling. What differs per ecosystem is only
what `package` matches, and the handful of deviations below.

| Ecosystem | `package` matches | Deviation from the rule |
| ----------- | ------------------- | ------------------------- |
| npm / yarn / pnpm / bun | package name | none — clamp is a targeted install |
| deno | Deno outdated identity (`npm:…`, `jsr:…`) | a prefix ceiling does not require its exact `.0` patch to exist |
| go | module path | Go's resolver never crosses a major on its own (major ≥2 lives in the path), so a hold caps a same-major minor/patch; an untagged module takes its exact pseudo-version as `max_version` (e.g. `v0.0.0-20260821135717-be32def86098`), ordered by base version then commit time, and the clamp pins that exact version |
| rust | Cargo crate name | clamp uses `cargo update --precise`; if no published version fits a prefix ceiling it fails and asks for an exact three-component `max_version` rather than assuming `1.5.0` exists for `1.5` |
| uv / hatch / pdm / pipenv / poetry | distribution name | no per-package targeted install exists, so the post-apply clamp is the *only* enforcement, not a safety net |
| pip-tools | distribution name | enforced by passing an ephemeral pip constraints file to the compile step; `requirements.in` is never modified |
| pre-commit | hook repo id (`example/hook`) | none |
| actions | action repo id (`actions/checkout`) | also blocks normalize-to-floating-`@vN` above the ceiling; SHA pins keep digest form when a matching tag exists; workspace scoping is repo-root only |
| container | image name, no tag or digest | Docker Hub aliases canonicalize, so `docker.io/library/node` matches `FROM node:…`; SHA-only pins without tag intent stay report-only |
| submodule | submodule path from `.gitmodules` | none |
