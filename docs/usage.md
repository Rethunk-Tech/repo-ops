# Usage

Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes, Go modules, and Yarn isolated plan for the `repo-ops` CLI.

## Subcommands

Subcommands (entry point in `packages/repo-ops`):

| Subcommand | Purpose |
|------------|---------|
| `inventory` | Read-only dependency footprint |
| `plan` | Outdated deps, Actions pin drift, remediations (no writes) |
| `apply` | Apply updates, Actions bumps, remediations, verify gates |
| `pushed` | Commits pushed to remotes in a time window (reflog) |
| `audit` | Fleet vulnerability sweep per ecosystem (read-only) |
| `dependabot` | List/close Dependabot PRs superseded by local manifests |
| `install-skill` | Copy bundled agent skill into Cursor/Claude skill dirs |

Default scan roots: [`DEFAULT_ROOTS`](../packages/repo-ops-core/src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. Discovery flags (`--flat`, `--follow-symlinks`, actions-only repos): [Security and discovery](#security-and-discovery).

`--json` on every subcommand. For `plan`/`apply`, stdout is one JSON object unless `--json-out PATH` is set (then stdout prints only that path and the report is written to `PATH` atomically). JSON reports include top-level `summary` counts (mirroring the human footer roll-up). Discovery notes live in `skipped` / `warnings` for JSON modes; human mode prints discovery lines to stderr. `--filter ECO` limits to `node`, `go`, `rust`, or `py`. `--submodules` adds `submodules[]` to plan/apply JSON; `apply --submodule-order=children-first` processes child git repos before parent umbrellas when `.gitmodules` is present.

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly). Node **check** runs `corepack prepare` (via `check_yarn_snapshot`) so the correct Yarn version is activated in the temporary workspace — plan has a corepack side-effect and may access the network.
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope. On verify failure, `--on-verify-fail=keep` (default) leaves the worktree as-is; `--on-verify-fail=revert` restores pre-apply lockfile snapshots and git-tracked paths under the workspace. Human and JSON workspace rows include `verify_fail_disposition` (`FAILED_WITH_CHANGES` vs `FAILED_CLEAN`) when verify fails.
- Actions pins included by default; `--skip-actions` for dependency-only runs.
- **Cross-major Actions bumps:** By default a newer major upstream (e.g. `actions/checkout@v4` → `v6`) is **report only**. `--actions-upgrade-mode=latest` (or the `--latest` umbrella) applies it: tag-style `major` rewrites to the floating `@v{max}` (only when a bare `v{max}` tag exists upstream — same safety check as normalize), tag-style `exact` rewrites to the highest `@v{max}.M.P`. When the newest major cannot be safely targeted, it falls back to normalize/report-only. An explicit `--actions-upgrade-mode` overrides the `--latest` umbrella. For SHA-pinned actions, combine `--actions-refresh-sha` with `--latest` to re-pin to the newest major's commit SHA (with comment update) — see SHA digest refresh below.
- **SHA pins (default: preserve):** By default, `uses: owner/repo@<sha>` pins are **report only** on apply (supply-chain hardening). Opt in to tag resolution with `--actions-unpin-sha`. When unpinning: (1) trailing `# vN[.N.N]` comment → use that major; (2) tags API SHA→tag lookup → highest semver tag for that commit. If neither resolves, the SHA is reported but left untouched. `--actions-tag-style=major` (default) → `@vN`; `exact` → highest `@vN.M.P` within the resolved major (falls back to `@vN`).
- **SHA digest refresh (keep pinned):** `--actions-refresh-sha` keeps a `uses: owner/repo@<sha>  # vN` action pinned **by digest** but advances the SHA to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` comment. Use this to supersede Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. Combined with `--latest` (or `--actions-upgrade-mode=latest`), also **crosses major boundaries**: re-pins to the newest major's commit SHA and updates the `# vN` comment to the new major (e.g. `# v6` → `# v7`) — superseding Dependabot's cross-major SHA-pin PRs such as `actions/checkout` v6→v7 or `sigstore/cosign-installer` v3→v4. Falls back to within-major refresh when the newest major has no exact `vN.M.P` tag. The major is resolved from the `# vN` comment, else the tags API. No effect with `--actions-unpin-sha` (that resolves the SHA to a tag instead); a no-op when the major can't be resolved or the pin is already the newest digest.
- Parallelism: `--jobs` must be a positive integer (default `8`).

### Security and discovery

- **Scan roots:** `resolve_scan_roots` expands paths, requires existing directories, and rejects tokens that look like `find(1)` predicates (for example `-name`).
- **Symlinks:** `find` does not follow symlinks by default. Pass `--follow-symlinks` on `plan`, `apply`, or `pushed` when symlinked `.git` dirs must be traversed (`find -L`).
- **Actions-only repos:** Git repos with workflow files but no dependency workspaces still appear in plan/apply (Actions pin audit only).
- **`--flat`:** On `inventory` and upgrade `plan`/`apply`, skips the org/proj two-level walk when scanning a single root (not used by `pushed`).
- **Node `packageManager`:** Only `npm`, `pnpm`, `yarn`, and `bun` with `name@version` form are accepted; path-like or unknown names are skipped with diagnostics.

### Ecosystem upgrade modes (plan and apply)

| Ecosystem | Default behavior | Aggressive / latest opt-in |
|-----------|------------------|----------------------------|
| **npm** | `npm update` (semver ranges in manifests) | — |
| **pnpm** | `pnpm update -r` (within ranges) | `REPO_OPS_PNPM_UPDATE_LATEST=1` → adds `--latest` |
| **yarn** | `yarn up` (Berry, in-range bumps in isolated plan snapshots) | `REPO_OPS_YARN_UPDATE_LATEST=1` → `yarn up -R '*'`; reports direct deps yarn's resolver kept behind their newest published version as `behind_latest` (#110). Add `--yarn-targeted-bump` (`REPO_OPS_YARN_TARGETED_BUMP=1`) to also *move* those gaps: a reversible per-package `yarn up <pkg>@latest`, kept only if the workspace still installs and the version actually advances (#111) |
| **bun** | `bun update` (within ranges) | `REPO_OPS_BUN_UPDATE_LATEST=1` → includes out-of-range "latest" packages |
| **go** | `REPO_OPS_GO_UPGRADE_MODE=patch` (default) | `explicit` or `mvs` (see below) |
| **rust** | `cargo update --dry-run` / `cargo update` | — |
| **python** | Manager-specific (`uv`, poetry, pip-tools, hatch) | See each plugin's check/apply |
| **GitHub Actions** | `--actions-upgrade-mode=normalize` (default): normalize exact tag pins to the floating major (`@vN`); a newer major upstream is reported only. SHA pins: report only (preserve digest). | `--actions-upgrade-mode=latest` (or `--latest`) → also apply cross-major bumps; `--actions-unpin-sha` → resolve SHA pins to tags; `--actions-refresh-sha` → advance digest within current major (with `--latest`: cross-major re-pin + comment update); `--actions-tag-style=exact` → highest `@vN.M.P` when unpinning |

### Go modules

`REPO_OPS_GO_UPGRADE_MODE` (default `patch`) aligns plan and apply:

| Mode | Plan | Apply |
|------|------|-------|
| `patch` (default) | `go list -m -u -json`, same minor-line patches only | `go get -u=patch all`, `go mod tidy` |
| `explicit` | all `list -u` updates | batched `go get path@version`, tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u -t all`, tidy |

### Yarn isolated plan

Isolated snapshots copy `.yarn/releases/` (Berry) and `file:../…` siblings from `package.json`; skip `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in plan JSON `error`. Upgrade command policy: table above (`REPO_OPS_YARN_UPDATE_LATEST`).

#### Targeted bump fallback (`--yarn-targeted-bump`, #111)

`yarn up -R '*'` delegates to yarn's resolver, which keeps peer/group-constrained direct deps below their newest release; `behind_latest` (#110) reports those. Opt into `--yarn-targeted-bump` (with `--latest`) to *move* the gap instead of only reporting it: for each `behind_latest` dep, repo-ops snapshots the lockfile/manifests, runs a targeted `yarn up <pkg>@latest` + `yarn install`, and keeps the result only when install succeeds **and** the resolved version actually advances. Any failure — install break, or a ghost bump where the lock churns but the resolver holds the old version — restores the snapshot, so the dep stays in `behind_latest` and nothing broken is committed. Successful bumps become normal planned changes (visible in `plan`, applied by `apply`).

### `audit` — fleet vulnerability sweep

`repo-ops audit [root_dir ...]` runs the native auditor for each ecosystem found
across discovered workspaces (read-only, no lockfile writes).

| Ecosystem | Tool required | Notes |
|-----------|--------------|-------|
| `go` | `govulncheck` | `govulncheck -json ./...`; workspace skipped if not on PATH |
| `node` | `npm` / `yarn` / `pnpm` / `bun` | detected from lockfile; `bun audit` text-parsed (no `--json`) |
| `py-uv` | `uv` + `pip-audit` | `uv run pip-audit --format json`; skipped when pip-audit not importable |
| `rust` | `cargo` + `cargo-audit` | `cargo audit --json`; skipped if `cargo audit --version` fails |

Counts are normalised to `{critical, high, moderate, low}` per workspace.
CVSS severity: govulncheck findings default to `high`; pip-audit maps CVE presence
to `high`/`critical`; cargo audit maps CVSS vector C/I/A components.

`--fail-on SEVERITY` (default `high`) controls the exit code:
- `0` when no workspace has vulns at or above the threshold.
- `1` when at least one workspace has qualifying vulns.
- `never` always exits 0 (CI-informational mode).

`--json` emits a structured payload with per-workspace counts and a `summary`.

### `dependabot` — Dependabot PR sweep

`repo-ops dependabot [root_dir ...]` discovers repos with a `github.com` remote,
lists open Dependabot PRs via `gh pr list --author app/dependabot`, and checks
whether the bump is already satisfied by local lockfiles or manifests.

**Default (dry-run):** prints the "would-close" list with supersession status.
**`--close`:** closes superseded PRs via `gh pr close <n> --comment "Superseded..."`.

Precision caveats:
- Version checks are text-search based on lockfile `name@version` strings (npm,
  yarn, pnpm, bun) and manifest declarations (go.mod `require` blocks,
  Cargo.lock `[[package]]`). False negatives possible for workspace-hoisted
  packages or non-standard lockfile entries.
- Title parsing covers `Bump X from A to B` / `Bumps X from A to B`; non-standard
  Dependabot titles are reported but never auto-closed.
- `gh` must be authenticated and on PATH; repos where `gh` fails are reported
  with an error and skipped.

`--json` emits per-repo PR lists with `superseded`, `current_ver`, and `closed` fields.

Run `repo-ops <subcommand> --help` for all flags.

## Settings reference

One layered lookup per setting: **CLI flag** → **env var** → **config file** → **default**.

Config files (TOML):
- **Global:** `~/.config/repo-ops/config.toml` (or `$XDG_CONFIG_HOME/repo-ops/config.toml`)
- **Per-repo:** `[tool.repo-ops]` table in `pyproject.toml` at the repo root

Python 3.11+ only; skipped with a warning on 3.10 unless `tomli` is installed.

| Setting | Flag | Env var | Config key | Default |
|---------|------|---------|------------|---------|
| jobs | `--jobs` | (none) | `jobs` | `min(16, cpu_count)` |
| yarn_max_concurrency | `--yarn-jobs` | `REPO_OPS_YARN_MAX_CONCURRENCY` | `yarn_max_concurrency` | `2` |
| go_upgrade_mode | `--go-upgrade-mode` | `REPO_OPS_GO_UPGRADE_MODE` | `go_upgrade_mode` | `patch` |
| actions_lint_level | `--actions-lint-level` | `REPO_OPS_ACTIONS_LINT_LEVEL` | `actions_lint_level` | `block` |
| timeout_check | (none) | `REPO_OPS_TIMEOUT_CHECK` | `timeout_check` | `60` |
| timeout_apply | (none) | `REPO_OPS_TIMEOUT_APPLY` | `timeout_apply` | `300` |
| timeout_verify | (none) | `REPO_OPS_TIMEOUT_VERIFY` | `timeout_verify` | `120` |
| find_timeout | (none) | `REPO_OPS_FIND_TIMEOUT` | `find_timeout` | `30` |
| git_timeout | (none) | `REPO_OPS_GIT_TIMEOUT` | `git_timeout` | `10` |
| bun_update_latest | `--latest` | `REPO_OPS_BUN_UPDATE_LATEST` | `bun_update_latest` | `0` |
| pnpm_update_latest | `--latest` | `REPO_OPS_PNPM_UPDATE_LATEST` | `pnpm_update_latest` | `0` |
| yarn_update_latest | `--latest` | `REPO_OPS_YARN_UPDATE_LATEST` | `yarn_update_latest` | `0` |
| yarn_targeted_bump | `--yarn-targeted-bump` | `REPO_OPS_YARN_TARGETED_BUMP` | `yarn_targeted_bump` | `0` |
| npm_targeted_bump | `--npm-targeted-bump` | `REPO_OPS_NPM_TARGETED_BUMP` | `npm_targeted_bump` | `0` |
| pnpm_targeted_bump | `--pnpm-targeted-bump` | `REPO_OPS_PNPM_TARGETED_BUMP` | `pnpm_targeted_bump` | `0` |
| go_work_sync | (none) | `REPO_OPS_GO_WORK_SYNC` | `go_work_sync` | `0` |
| no_progress | (none) | `REPO_OPS_NO_PROGRESS` | `no_progress` | `0` |
| no_state | (none) | `REPO_OPS_NO_STATE` | `no_state` | `0` |
| no_lint_cache | (none) | `REPO_OPS_NO_LINT_CACHE` | `no_lint_cache` | `0` |
| go_directive | (none) | `REPO_OPS_GO_DIRECTIVE` | `go_directive` | (none) |
| go_toolchain | (none) | `REPO_OPS_GO_TOOLCHAIN` | `go_toolchain` | (none) |
| node_verify_script | (none) | `REPO_OPS_NODE_VERIFY_SCRIPT` | `node_verify_script` | (none) |
| commit_agent_provider | (none) | `REPO_OPS_COMMIT_AGENT_PROVIDER` | `commit_agent_provider` | `cursor` |
| exclude_patterns | `--exclude` | (none) | `exclude` | `[]` |
| commit_umbrella | `--commit-umbrella` | `REPO_OPS_COMMIT_UMBRELLA` | `commit_umbrella` | `0` |
| actions_org_policy | `--actions-org-policy` | `REPO_OPS_ACTIONS_ORG_POLICY` | `actions_org_policy` | `0` |
| container_update_latest | (none) | `REPO_OPS_CONTAINER_UPDATE_LATEST` | `container_update_latest` | `0` |
