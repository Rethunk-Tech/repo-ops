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
| `install-skill` | Copy bundled agent skill into Cursor/Claude skill dirs |

Default scan roots: [`DEFAULT_ROOTS`](../packages/repo-ops-core/src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. Discovery flags (`--flat`, `--follow-symlinks`, actions-only repos): [Security and discovery](#security-and-discovery).

`--json` on every subcommand. For `plan`/`apply`, stdout is one JSON object unless `--json-out PATH` is set (then stdout prints only that path and the report is written to `PATH` atomically). JSON reports include top-level `summary` counts (mirroring the human footer roll-up). Discovery notes live in `skipped` / `warnings` for JSON modes; human mode prints discovery lines to stderr. `--filter ECO` limits to `node`, `go`, `rust`, or `py`. `--submodules` adds `submodules[]` to plan/apply JSON; `apply --submodule-order=children-first` processes child git repos before parent umbrellas when `.gitmodules` is present.

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly). Node **check** runs `corepack prepare` (via `check_yarn_snapshot`) so the correct Yarn version is activated in the temporary workspace — plan has a corepack side-effect and may access the network.
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope. On verify failure, `--on-verify-fail=keep` (default) leaves the worktree as-is; `--on-verify-fail=revert` restores pre-apply lockfile snapshots and git-tracked paths under the workspace. Human and JSON workspace rows include `verify_fail_disposition` (`FAILED_WITH_CHANGES` vs `FAILED_CLEAN`) when verify fails.
- Actions pins included by default; `--skip-actions` for dependency-only runs.
- **Cross-major Actions bumps:** By default a newer major upstream (e.g. `actions/checkout@v4` → `v6`) is **report only**. `--actions-upgrade-mode=latest` (or the `--latest` umbrella) applies it: tag-style `major` rewrites to the floating `@v{max}` (only when a bare `v{max}` tag exists upstream — same safety check as normalize), tag-style `exact` rewrites to the highest `@v{max}.M.P`. When the newest major cannot be safely targeted, it falls back to normalize/report-only. An explicit `--actions-upgrade-mode` overrides the `--latest` umbrella.
- **SHA unpinning:** By default, `uses: owner/repo@<40-hex-sha>` pins are resolved to version tags and rewritten on `apply`. Resolution order: (1) trailing `# vN[.N.N]` comment → use that major; (2) tags API SHA→tag lookup → pick the highest semver tag whose commit SHA matches. If neither resolves (no mapping, no comment), the SHA is reported but left untouched. `--actions-keep-sha` disables all SHA rewrites (restores report-only mode). `--actions-tag-style=major` (default) → `@vN`; `--actions-tag-style=exact` → highest `@vN.M.P` within the resolved major (falls back to `@vN` when no exact tags exist). Supply-chain note: SHA pins are immutable — the tag style trades immutability for Dependabot/renovate compatibility; this is an intentional project preference (tags are mutable but practical).
- **SHA digest refresh (keep pinned):** `--actions-refresh-sha` keeps a `uses: owner/repo@<sha>  # vN` action pinned **by digest** but advances the SHA to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` comment. Use this to supersede Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. The major is resolved from the `# vN` comment, else the tags API. No effect with `--actions-unpin-sha` (that resolves the SHA to a tag instead); a no-op when the major can't be resolved or the pin is already the newest in-major digest.
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
| **yarn** | `yarn up` (Berry, in-range bumps in isolated plan snapshots) | `REPO_OPS_YARN_UPDATE_LATEST=1` → `yarn up -R '*'` |
| **bun** | `bun update` (within ranges) | `REPO_OPS_BUN_UPDATE_LATEST=1` → includes out-of-range "latest" packages |
| **go** | `REPO_OPS_GO_UPGRADE_MODE=patch` (default) | `explicit` or `mvs` (see below) |
| **rust** | `cargo update --dry-run` / `cargo update` | — |
| **python** | Manager-specific (`uv`, poetry, pip-tools, hatch) | See each plugin's check/apply |
| **GitHub Actions** | `--actions-upgrade-mode=normalize` (default): normalize exact tag pins to the floating major (`@vN`); a newer major upstream is reported only. SHA pins: resolve via comment hint or tags API → rewrite to `@vN` (default). | `--actions-upgrade-mode=latest` (or `--latest`) → also apply cross-major bumps to the newest available major; `--actions-tag-style=exact` → rewrite to highest `@vN.M.P`; `--actions-keep-sha` → report only (no rewrite) |

### Go modules

`REPO_OPS_GO_UPGRADE_MODE` (default `patch`) aligns plan and apply:

| Mode | Plan | Apply |
|------|------|-------|
| `patch` (default) | `go list -m -u -json`, same minor-line patches only | `go get -u=patch all`, `go mod tidy` |
| `explicit` | all `list -u` updates | batched `go get path@version`, tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u -t all`, tidy |

### Yarn isolated plan

Isolated snapshots copy `.yarn/releases/` (Berry) and `file:../…` siblings from `package.json`; skip `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in plan JSON `error`. Upgrade command policy: table above (`REPO_OPS_YARN_UPDATE_LATEST`).

Run `repo-ops <subcommand> --help` for all flags.
