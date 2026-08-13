# Usage

Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes, Go modules, and Yarn isolated plan for the `repo-ops` CLI.

## Subcommands

Subcommands (entry point in `packages/repo-ops`):

| Subcommand | Purpose |
| ------------ | --------- |
| `inventory` | Read-only dependency footprint |
| `plan` | Outdated deps, Actions pin drift, remediations (no writes) |
| `apply` | Apply updates, Actions bumps, remediations, verify gates |
| `pushed` | Commits in a time window from local remote-tracking reflogs (default) or GitHub PushEvents (`--source=gh`) |
| `audit` | Fleet vulnerability sweep per ecosystem (read-only unless `--fix`) |
| `dependabot` | List/close Dependabot PRs superseded by local manifests |
| `sbom` | Emit CycloneDX/SPDX SBOM from lockfile dependencies (read-only) |
| `license` | Scan dependency licenses and flag policy violations (read-only) |
| `doctor` | Toolchain/auth preflight: probe known binaries and `gh` auth (read-only) |
| `install-skill` | Copy bundled agent skill into Cursor/Claude skill dirs |

Default scan roots: [`DEFAULT_ROOTS`](../packages/repo-ops-core/src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. Discovery flags (`--flat`, `--follow-symlinks`, actions-only repos): [Security and discovery](#security-and-discovery).

`--json` is available on every subcommand except `sbom` (which emits its report via `--format`/`--output` instead). `--json-out PATH` (writes the report atomically to `PATH`; stdout prints only that path) is available on `inventory`, `plan`, `apply`, `pushed`, and `doctor` — for `plan`/`apply`, stdout is otherwise one JSON object. JSON reports include top-level `summary` counts (mirroring the human footer roll-up). Discovery notes live in `skipped` / `warnings` for JSON modes; human mode prints discovery lines to stderr. `--filter ECO` limits to one ecosystem: `node`, `go`, `rust`, `py`, `container`, `precommit`, `actions`, or `deno`. `--log-level {debug,info,warning,error}` sets the stdlib root logger level; `--verbose` / `-v` enables subprocess echo and implies `debug` when `--log-level` is absent. `--submodules` adds `submodules[]` to plan/apply JSON; `apply --submodule-order=children-first` processes child git repos before parent umbrellas when `.gitmodules` is present.

### `pushed` — local reflog or GitHub PushEvent

`repo-ops pushed` defaults to this machine's `refs/remotes/*` reflog entries
whose message is `update by push`. On that default (`--source=local`), a fresh
clone, a push from another workstation, or a CI push can show zero even when
GitHub records activity.

Pass `--source=gh` to query authenticated GitHub `PushEvent` activity for each
GitHub remote instead. When a PushEvent omits its `commits` array (common for
large pushes), collection falls back to the authenticated compare API between
`before` and `head`. Repos with no `github.com` remote emit a stderr warning
and are called out in the human summary rather than looking like a true empty
window. GitHub API or auth failures exit non-zero rather than reporting zero
pushes.

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly). Node **check** runs `corepack prepare` (via `check_yarn_snapshot`) so the correct Yarn version is activated in the temporary workspace — plan has a corepack side-effect and may access the network.
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope. On verify failure, `--on-verify-fail=keep` (default) leaves the worktree as-is; `--on-verify-fail=revert` restores pre-apply lockfile snapshots and git-tracked paths under the workspace. Human and JSON workspace rows include `verify_fail_disposition` (`FAILED_WITH_CHANGES` vs `FAILED_CLEAN`) when verify fails.
- **Verification tests:** `--verify-test` runs the additional Go and Rust test gates during apply verification. Go tests run with the Go verify gates, and Rust runs `cargo test` after `cargo check`; the default remains off.
- Actions pins included by default; `--skip-actions` for dependency-only runs.
- **Cross-major Actions bumps:** By default a newer major upstream (e.g. `actions/checkout@v4` → `v6`) is **report only**. `--actions-upgrade-mode=latest` (or the `--latest` umbrella) applies it: tag-style `major` rewrites to the floating `@v{max}` (only when a bare `v{max}` tag exists upstream — same safety check as normalize), tag-style `exact` rewrites to the highest `@v{max}.M.P`. When the newest major cannot be safely targeted, it falls back to normalize/report-only. An explicit `--actions-upgrade-mode` overrides the `--latest` umbrella. For SHA-pinned actions, combine `--actions-refresh-sha` with `--latest` to re-pin to the newest major's commit SHA (with comment update) — see SHA digest refresh below.
- **SHA pins (default: preserve):** By default, `uses: owner/repo@<sha>` pins are **report only** on apply (supply-chain hardening). Opt in to tag resolution with `--actions-unpin-sha`. When unpinning: (1) trailing `# vN[.N.N]` comment → use that major; (2) tags API SHA→tag lookup → highest semver tag for that commit. If neither resolves, the SHA is reported but left untouched. `--actions-tag-style=major` (default) → `@vN`; `exact` → highest `@vN.M.P` within the resolved major (falls back to `@vN`).
- **SHA digest refresh (keep pinned):** `--actions-refresh-sha` keeps a `uses: owner/repo@<sha>  # vN` action pinned **by digest** but advances the SHA to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` comment. Use this to supersede Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. Combined with `--latest` (or `--actions-upgrade-mode=latest`), also **crosses major boundaries**: re-pins to the newest major's commit SHA and updates the `# vN` comment to the new major (e.g. `# v6` → `# v7`) — superseding Dependabot's cross-major SHA-pin PRs such as `actions/checkout` v6→v7 or `sigstore/cosign-installer` v3→v4. Falls back to within-major refresh when the newest major has no exact `vN.M.P` tag. The major is resolved from the `# vN` comment, else the tags API. No effect with `--actions-unpin-sha` (that resolves the SHA to a tag instead); a no-op when the major can't be resolved or the pin is already the newest digest.
- **Pre-commit SHA refresh:** `--precommit-refresh-sha` keeps resolvable SHA-pinned `rev:` entries pinned by digest while advancing them to the newest release within the tagged major for the same hook repository. Without the flag, SHA pins remain report only; unresolvable pins remain unchanged.
- **Submodule SHA refresh:** `--submodule-refresh-sha` keeps SHA-only submodule HEADs pinned by commit while advancing them to the newest resolvable release within the tagged major for the same `.gitmodules` URL. Without the flag, SHA-only pointers remain report only; unresolvable pins remain unchanged.
- **Full coverage (`--full`):** This opt-in umbrella enables `--latest`, `--actions-refresh-sha`, `--container-refresh-sha`, `--precommit-refresh-sha`, `--submodule-refresh-sha`, and all five targeted-bump flags (`--yarn-targeted-bump`, `--npm-targeted-bump`, `--pnpm-targeted-bump`, `--bun-targeted-bump`, and `--deno-targeted-bump`). It does not change defaults when absent. `--latest` alone is incomplete: it does not refresh SHA-pinned Actions, container digests, pre-commit revisions, or submodule pointers and does not close yarn/npm/pnpm/bun/deno `behind_latest` gaps.
- **Human-only filtering (`--only-outdated`):** Human-readable output can be limited to entries with outdated dependencies. JSON output remains complete and unfiltered; `--only-outdated` does not remove rows from JSON reports.
- **Remediations:** `--skip-remediations` accepts the current remediation IDs below. Remediations are enabled by default unless marked off.

| ID | Ecosystem | Trigger | Default |
| ---- | --------- | ------- | ------- |
| `biome_schema` | Node | Biome schema remediation | on |
| `playwright_browsers` | Node | Playwright browser remediation | on |
| `codegen` | Node | Configured code-generation command | on |
| `package_manager_pin` | Node | `packageManager` pin remediation | on |
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
- **Symlinks:** `find` does not follow symlinks by default. Pass `--follow-symlinks` on `plan`, `apply`, or `pushed` when symlinked `.git` dirs must be traversed (`find -L`).
- **Actions-only repos:** Git repos with workflow files but no dependency workspaces still appear in plan/apply (Actions pin audit only).
- **`--flat`:** On `inventory` and upgrade `plan`/`apply`, skips the org/proj two-level walk when scanning a single root (not used by `pushed`).
- **Node `packageManager`:** Only `npm`, `pnpm`, `yarn`, and `bun` with `name@version` form are accepted; path-like or unknown names are skipped with diagnostics.
- **`--exclude PATTERN`:** Repeatable on `plan`, `apply`, `audit`, `dependabot`, and `pushed`. A repository is skipped when a pattern substring- or fnmatch-matches its label or absolute path.

When JSON is enabled, audit and pushed report excluded repositories in
`summary.excluded`; dependabot uses the same canonical `summary.excluded` key.

### Ecosystem upgrade modes (plan and apply)

| Ecosystem | Default behavior | Aggressive / latest opt-in |
| ----------- | ------------------ | ---------------------------- |
| **npm** | `npm update` (semver ranges in manifests) | Add `--npm-targeted-bump` (`REPO_OPS_NPM_TARGETED_BUMP=1`) to move deps left behind when a latest-mode bump was attempted |
| **pnpm** | `pnpm update -r` (within ranges) | `REPO_OPS_PNPM_UPDATE_LATEST=1` → adds `--latest`; add `--pnpm-targeted-bump` (`REPO_OPS_PNPM_TARGETED_BUMP=1`) for behind-latest fallback, including `catalog:` / `catalogs:` entries in `pnpm-workspace.yaml` |
| **yarn** | `yarn up` (Berry, in-range bumps in isolated plan snapshots) | `REPO_OPS_YARN_UPDATE_LATEST=1` → `yarn up -R '*'`; reports direct deps yarn's resolver kept behind their newest published version as `behind_latest` (#110). Add `--yarn-targeted-bump` (`REPO_OPS_YARN_TARGETED_BUMP=1`) to also *move* those gaps: a reversible per-package `yarn up <pkg>@latest`, kept only if the workspace still installs and the version actually advances (#111) |
| **bun** | `bun update` (within ranges) | `REPO_OPS_BUN_UPDATE_LATEST=1` → includes out-of-range "latest" packages. Add `--bun-targeted-bump` (`REPO_OPS_BUN_TARGETED_BUMP=1`) to also *move* deps `bun update --latest` left behind: a reversible per-package `bun add <pkg>@latest`, kept only if the workspace still installs and the version actually advances |
| **go** | `REPO_OPS_GO_UPGRADE_MODE=patch` (default) | `explicit` or `mvs` (see below) |
| **rust** | `cargo update --dry-run` / `cargo update` (within semver constraints in `Cargo.lock`) | No cross-range / `--latest` mode — `--latest` does not change cargo behavior. Opt into direct Cargo.toml constraint bumps with `--rust-targeted-bump` (`REPO_OPS_RUST_TARGETED_BUMP=1`); this flag is not part of `--full`. Rust major upgrades are out of scope for repo-ops; edit manifests by hand |
| **python** | Manager-specific (`uv`, poetry, pip-tools, hatch) | uv stays constraint-respecting (`uv lock --upgrade`). Poetry/PDM/Pipenv/pip-tools have no `--latest` umbrella; opt into constraint bumps with their targeted-bump flags (not part of `--full`) |
| **poetry** | `poetry update --dry-run` / `poetry update` (in-range); skipped with a WARN when `poetry` is not on PATH | `--poetry-targeted-bump` (`REPO_OPS_POETRY_TARGETED_BUMP=1`) — reversible per-package `poetry add pkg@^X` when the lock is behind a published version that needs a constraint bump. Not implied by `--latest` or `--full` |
| **pdm** | `pdm update --dry-run` / `pdm update` (in-range); skipped with a WARN when `pdm` is not on PATH | `--pdm-targeted-bump` (`REPO_OPS_PDM_TARGETED_BUMP=1`) — reversible per-package `pdm add pkg@^X`. Not implied by `--latest` or `--full` |
| **pipenv** | venv `pip list --outdated --format=json` (via `pipenv --venv`, read-only; never creates a venv) / `pipenv update`; skipped with a WARN when `pipenv` is not on PATH or the venv is not initialised | `--pipenv-targeted-bump` (`REPO_OPS_PIPENV_TARGETED_BUMP=1`) — reversible per-package `pipenv install`, only when an existing venv is detected; not implied by `--latest` or `--full` |
| **pip-tools** | `uv pip compile` or `pip-compile` from `requirements.in` (within constraints) | `--pip-tools-targeted-bump` (`REPO_OPS_PIP_TOOLS_TARGETED_BUMP=1`) — rewrites `requirements.in` only when its constraint blocks the latest published version, then recompiles reversibly; not implied by `--latest` or `--full` |
| **deno** | `deno outdated` / `deno outdated --update` (Deno ≥ 2.x); supports both box-drawing and plain-text output; skipped with a WARN when `deno` is not on PATH | `REPO_OPS_DENO_UPDATE_LATEST=1` → adds `--latest`; add `--deno-targeted-bump` (`REPO_OPS_DENO_TARGETED_BUMP=1`) for behind-latest fallback via selective `deno update <pkg>` |
| **pre-commit** | Checks `.pre-commit-config.yaml` hook revs against latest upstream tags (within-major only by default); SHA pins are report-only | `REPO_OPS_PRECOMMIT_UPDATE_LATEST=1` → allow cross-major rev bumps; `--precommit-refresh-sha` (`REPO_OPS_PRECOMMIT_REFRESH_SHA=1`) → refresh resolvable SHA pins within their tagged major |
| **GitHub Actions** | `--actions-upgrade-mode=normalize` (default): normalize exact tag pins to the floating major (`@vN`); a newer major upstream is reported only. SHA pins: report only (preserve digest). | `--actions-upgrade-mode=latest` (or `--latest`) → also apply cross-major bumps; `--actions-unpin-sha` → resolve SHA pins to tags; `--actions-refresh-sha` → advance digest within current major (with `--latest`: cross-major re-pin + comment update); `--actions-tag-style=exact` → highest `@vN.M.P` when unpinning. With `--actions-org-policy`, `--actions-policy-level=report | warn | block` (default `report`) controls whether violating pin rewrites are annotated only, warned, or blocked (`policy-blocked`) |
| **container** | Checks `FROM` / `image:` references in Dockerfiles and Compose files; SHA-pinned images are report-only by default | `REPO_OPS_CONTAINER_UPDATE_LATEST=1` (or `--latest`) → allow cross-major tag bumps; `REPO_OPS_CONTAINER_REFRESH_SHA=1` → advance SHA digest for `image:tag@sha256:…` pins to the latest digest for the same tag (keeps pin hardened, advances to current content) |

#### Hatch and `--migrate-to-uv-lock`

Hatch workspaces without a `uv.lock` are not upgraded unless `--migrate-to-uv-lock` (or `REPO_OPS_HATCH_MIGRATE_UV_LOCK=1`) opts in to creating one. On apply **verify**, when hatch has no `uv.lock`, verify intentionally skips `uv sync --locked` (nothing to lock against until migration) and runs only hatch fast gates (`check`/`typecheck` scripts, ruff, mypy, and pyrefly when configured). When `[tool.pyrefly]` configures `project-excludes` or `project-includes`, verify passes those paths as pyrefly CLI flags so the project-defined scope is honored without hardcoded exclusions. After a successful lock gate, **uv** and **hatch** workspaces run the fast gate stack via `uv run`; **poetry**, **pdm**, and **pipenv** invoke it via `poetry run`, `pdm run`, and `pipenv run`, respectively. Missing manager binaries fail closed with a clear reason.

### Go modules

`REPO_OPS_GO_UPGRADE_MODE` (default `patch`) aligns plan and apply:

| Mode | Plan | Apply |
| ------ | ------ | ------- |
| `patch` (default) | `go list -m -u -json`, same minor-line patches only | `go get -u=patch all`, `go mod tidy` |
| `explicit` | all `list -u` updates | batched `go get path@version`, tidy |
| `mvs` | legacy `go list -u -m all` text | `go get -u -t all`, tidy |

### Yarn isolated plan

Isolated snapshots copy `.yarn/releases/` (Berry) and `file:../…` siblings from `package.json`; skip `node_modules`, `.yarn/cache`, and other heavy dirs. Failed `yarn up` / `install` errors include a stderr tail in plan JSON `error`. Upgrade command policy: table above (`REPO_OPS_YARN_UPDATE_LATEST`).

#### Targeted bump fallback (`--yarn-targeted-bump`, #111)

`yarn up -R '*'` delegates to yarn's resolver, which keeps peer/group-constrained direct deps below their newest release; `behind_latest` (#110) reports those. Opt into `--yarn-targeted-bump` (with `--latest`) to *move* the gap instead of only reporting it: for each `behind_latest` dep, repo-ops snapshots the lockfile/manifests, runs a targeted `yarn up <pkg>@latest` + `yarn install`, and keeps the result only when install succeeds **and** the resolved version actually advances. Any failure — install break, or a ghost bump where the lock churns but the resolver holds the old version — restores the snapshot, so the dep stays in `behind_latest` and nothing broken is committed. Successful bumps become normal planned changes (visible in `plan`, applied by `apply`). Same shape for npm/pnpm/bun via their own `--*-targeted-bump` flags.

A `resolutions`/`overrides`/`pnpm.overrides` manifest pin blocking the target is not automatically a hard stop: if no [package hold](#package-holds--capping-a-packages-version-temporarily) covers the package, the retry releases that pin first (it predates the hold feature, or was a one-off fix never promoted to a tracked hold) before running the install — reverted along with everything else in the snapshot if the retry still doesn't land. A package covered by an active hold keeps its pin untouched and is bumped only up to the hold's ceiling. This applies to the *initial* bulk-update ghost-bump guard too (yarn/bun): with targeted-bump enabled, a blocked target from that first pass is handed to this retry instead of failing the whole workspace outright — the guard only raises immediately when there is no targeted-bump fallback to hand it to. Deno uses the same opt-in shape via `--deno-targeted-bump` (selective `deno update <pkg>` with lock/import-map snapshot restore).

### Submodule tag-advance

When `--submodules` is active, `plan` detects available semver tag advances for each discovered submodule (comparing the current registered commit against upstream tags). By default only **within-major** advances are surfaced. Set `REPO_OPS_SUBMODULE_UPDATE_LATEST=1` to also surface and apply **cross-major** tag advances. Set `--submodule-refresh-sha` (`REPO_OPS_SUBMODULE_REFRESH_SHA=1`) to refresh SHA-only HEADs when the same `.gitmodules` URL resolves their tagged major. `apply --submodule-order=children-first` applies submodule pointer updates before parent umbrella commits.

### Apply resume

Use `--resume` or set `REPO_OPS_RESUME=1` on `apply` to resume an interrupted bulk run without re-applying repos that already succeeded. Resume requires `--commit=conventional` or `--commit=agent`; `REPO_OPS_RESUME=1` alone writes nothing without `--commit`. After each repo's apply and verify succeed and its requested commit completes, `apply` writes the post-commit git HEAD SHA to a checkpoint file in the XDG cache directory. On re-run with resume enabled, repos whose current HEAD matches the checkpointed SHA are skipped as `resume: already applied`. If the HEAD has changed since the checkpoint (e.g. a commit landed in the meantime), the checkpoint entry is ignored and the repo is processed normally. To clear a stale checkpoint, delete `~/.cache/repo-ops/apply-checkpoint.json`.

### Branch / PR workflow (apply)

After a successful apply+verify pass with commits, opt into branch-and-PR mode instead of leaving changes on the current branch:

- **`--pr`** — create a branch, push, and open a PR via `gh` for each repo that received a commit (requires authenticated `gh`). Default: off (in-place commit only, no push).
- **`--pr-base BRANCH`** — base branch for opened PRs (default: repo default branch from `gh`).
- **`--branch-prefix PREFIX`** — branch name prefix (default: `repo-ops/deps`; resulting name `{prefix}/{repo-slug}`).

Idempotent when an open PR already exists for the same branch. Combine with `--commit=conventional` or `--commit=agent` so there are commits to push. See [Bulk maintenance](bulk-maintenance.md) for MCP commit guidance.

### `audit` — fleet vulnerability sweep

`repo-ops audit [root_dir ...]` runs the native auditor for each ecosystem found
across discovered workspaces. Default behavior is read-only (no lockfile writes);
`--fix` applies advisory remediations via the upgrade orchestration layer (mutates
lockfiles; no auto-commit).

| Ecosystem | Tool required | Notes |
| ----------- | -------------- | ------- |
| `go` | `govulncheck` | `govulncheck -json ./...`; workspace skipped if not on PATH |
| `node` | `npm` / `yarn` / `pnpm` / `bun` | detected from lockfile; `bun audit` text-parsed (no `--json`) |
| `py-uv` | `uv` + `pip-audit` | `uv run pip-audit --format json`; skipped when pip-audit not importable. When `uv.lock` and hatchling metadata both exist, `uv.lock` wins (`py-uv` / `uv`). |
| `py-poetry` / `py-pdm` / `py-pipenv` / `py-hatch` / `py-pip-tools` | manager + `pip-audit` | manager-native export then `pip-audit`; JSON `skip_reason` when export/tool missing (never false-clean `ok`). `py-hatch` only when hatch signals exist and no competing lock (`uv.lock` / poetry / pdm / Pipfile). |
| `rust` | `cargo` + `cargo-audit` | `cargo audit --json`; skipped if `cargo audit --version` fails |
| `deno` | `deno` | `deno audit --json` when supported, otherwise `deno audit`; skipped when not on PATH |

Counts are normalised to `{critical, high, moderate, low, unknown}` per workspace.
CVSS severity: govulncheck findings default to `unknown` (use `--fail-on-unknown`);
pip-audit maps CVE presence to `high`/`critical`; cargo audit maps CVSS vector
C/I/A components.

**Flags** (see `repo-ops audit --help` for the full list):

- **`--fail-on SEVERITY`** (default `high`) — exit `1` when any workspace has vulns at or above the threshold (`critical`/`high`/`moderate`/`low`/`never`).
- **`--fail-on-unknown`** — exit `1` when any workspace has govulncheck findings with unknown severity (default: off).
- **`--fix-plan`** — read-only remediation checklist: for each finding with a known-safe fix version, emit `(package, safe_version, ecosystem, workspace)`. With `--json`, included under the `remediations` key. Exits `1` when actionable remediations exist.
- **`--fix`** — apply remediations (implies `--fix-plan` collection). Delegates to upgrade orchestration per workspace; skips dirty worktrees unless **`--allow-dirty`**. Verifies each applied workspace by default; pass `--no-verify` to skip post-apply verification. No automatic commit.
- **`--sarif-out PATH`** — write SARIF 2.1.0 output for GitHub code scanning (repo-relative artifact URIs). Findings at or above `--fail-on` are emitted; `--fail-on-unknown` includes unknown-severity findings. Unfixed findings are marked with `properties.unfixed=true` and omit remediation `fixes`.
- **`--json`** — structured payload with per-workspace counts, optional `remediations[]`, and `summary`. When repositories are excluded, audit JSON reports the count as `summary.excluded`.

Each JSON workspace row may include `findings[]`, serialized from
`FindingItem.as_dict`, with finding identifier, package/version data, severity,
description, and available remediation details. SARIF uses these same findings
as its results; `--fail-on` controls the exit status and the findings selected
for SARIF, not whether JSON includes `findings[]`.

**Exit codes:**

- `0` — no vulns at or above `--fail-on`, and (when `--fix-plan` is active) no actionable remediations.
- `1` — at least one workspace qualifies under `--fail-on` / `--fail-on-unknown`, or `--fix-plan` found actionable remediations.

Config keys `audit_fail_on` / `audit_fail_on_unknown` (env: `REPO_OPS_AUDIT_FAIL_ON`, `REPO_OPS_AUDIT_FAIL_ON_UNKNOWN`) follow CLI > env > config > default precedence.

### `dependabot` — Dependabot PR sweep

`repo-ops dependabot [root_dir ...]` discovers repos with a `github.com` remote,
lists open Dependabot PRs via `gh pr list --author app/dependabot`, and checks
whether the bump is already satisfied by local lockfiles or manifests.

**Default (dry-run):** prints the "would-close" list with supersession status.
**`--close`:** closes superseded PRs via `gh pr close <n> --comment "Superseded..."`.
Mutually exclusive with **`--scaffold`** (scaffold mode does not scan PRs).

**Scaffold mode** (generate `.github/dependabot.yml` from detected ecosystems):

- **`--scaffold`** — dry-run: print the YAML that would be written per repo.
- **`--write`** — with `--scaffold`, actually write `.github/dependabot.yml` (requires `--scaffold`).
- **`--schedule INTERVAL`** — schedule for generated entries (`daily`/`weekly`/`monthly`; default `weekly`).

Scaffold detection covers npm, pnpm (`pnpm-lock.yaml` → `package-ecosystem: pnpm`),
Bun lockfiles (`package-ecosystem: bun`), Deno, pre-commit, pip, Go modules, Cargo,
Docker (`Dockerfile`, `docker-compose.yml` / `.yaml`, `compose.yml` / `.yaml`),
and GitHub Actions. This is separate
from supersession parsing, which determines whether existing Dependabot PRs are
already satisfied by local manifests or lockfiles.

`--json` and `--close` are invalid with `--scaffold`.

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

The shared `--exclude PATTERN` filter also applies to this sweep and to
`pushed`; repeat it to skip repositories whose label or absolute path matches.

### `sbom` — CycloneDX/SPDX SBOM export

`repo-ops sbom [root_dir ...]` builds a Software Bill of Materials from
discovered lockfile dependencies (read-only). Offline by default; component
licenses are `NOASSERTION` unless enrichment is enabled.
The default CycloneDX JSON uses specification 1.5; the tool component uses
`publisher` and `authors[]` (not the 1.4 `author` string). CycloneDX 1.4
consumers that require `metadata.tools[]` are dropped.

- `--format cyclonedx|spdx` — output format (default: `cyclonedx`).
- `--output PATH` — write the SBOM to `PATH` instead of stdout.
- `--compact` — emit single-line JSON instead of pretty-printed.
- `--enrich-licenses` — opt-in SPDX fill from on-disk package metadata and
  cached registry lookups (`REPO_OPS_ENRICH_LICENSES=1`), including JSR
  (`jsr.io` version meta, then `jsr.json` / `deno.json`). Never invents
  license ids; container images stay unenriched.
- `--enrich-licenses-local` — same enrichment from local metadata and XDG
  cache only (`REPO_OPS_ENRICH_LICENSES_LOCAL=1`); no registry network.
- `--enrich-licenses-network` — force registry network enrichment even when
  LOCAL env/flag is set; wins over `--enrich-licenses-local`
  (`REPO_OPS_ENRICH_LICENSES_NETWORK=1`).
  When local and network modes are both set, network mode wins.
  Implies enrichment is on. `BSD-3-Clause` requires the endorse/promote
  clause; redistribution text without that clause classifies as
  `BSD-2-Clause` (truncated BSD-3 without the third clause is indistinguishable).

### `license` — dependency license policy scan

`repo-ops license [root_dir ...]` classifies dependency licenses and flags
policy violations (read-only). Offline by default.

- `--allow LIST` — comma-separated SPDX identifiers that are permitted; any
  license not in this list is flagged.
- `--deny LIST` — comma-separated SPDX identifiers that are forbidden; any
  license in this list is flagged.
- `--no-fail-unknown` — exit `0` even when unknown licenses are found
  (default: exit `2` when unknown licenses exist in ecosystems with a local extractor).
- `--enrich-licenses` — same opt-in enrichment as `sbom` (env twin
  `REPO_OPS_ENRICH_LICENSES`); can reduce UNKNOWN for npm/PyPI/crates/JSR when
  metadata or cache hits exist.
- `--enrich-licenses-local` — local/cache-only enrichment (env twin
  `REPO_OPS_ENRICH_LICENSES_LOCAL`); when both enrichment flags (or both
  environment variables) are set, local mode wins unless network mode is
  explicitly forced. `--enrich-licenses-network` — force registry network
  enrichment even when LOCAL env/flag is set; wins over
  `--enrich-licenses-local` (`REPO_OPS_ENRICH_LICENSES_NETWORK=1`). BSD text
  classification is best-effort: `BSD-3-Clause` requires the endorse/promote
  clause; truncated BSD-3 text missing that clause is indistinguishable from
  `BSD-2-Clause` and classifies as `BSD-2-Clause`.
- `--json` — emit JSON instead of human-readable output (`--compact` for
  single-line).

**Exit codes:** `0` — no violations and no unknown licenses (or `--no-fail-unknown`);
`1` — policy violations (denied or not in allow-list); `2` — unknown licenses in
an ecosystem with a local extractor (container images are reported unknown but
excluded from this bucket).

**Scope (best-effort, offline):**

| Ecosystem | Source |
| ----------- | -------- |
| npm / yarn / pnpm | `node_modules/**/package.json` (requires prior install) |
| Python (uv.lock) | locked packages + adjacent venv `*.dist-info/METADATA` |
| Go | `go.sum` pairs via LICENSE files in GOMODCACHE |
| Rust | `Cargo.lock` via `cargo metadata --offline` |
| Deno | `deno.lock` `npm:` specifiers via DENO_DIR npm cache; `jsr:` ids fill only with `--enrich-licenses` |
| container | no local license source (always unknown; does not trigger exit `2`) |

Poetry, PDM, and pipenv lockfiles without an installed venv may report unknown
for Python packages. See `repo-ops license --help` epilog for SPDX compound-expression handling.

### `doctor` — toolchain/auth preflight

`repo-ops doctor` probes the binaries repo-ops shells out to across all
ecosystems (read-only; no scan roots, no `root_dir` arguments): `git`, `gh`,
`uv`, `pip-compile`, `poetry`, `pipenv`, `pdm`, `pip-audit`, `pyrefly`, `ruff`, `hatch`,
`pre-commit`, `npm`, `pnpm`, `yarn`, `bun`, `corepack`, `deno`, `go`, `gofmt`,
`govulncheck`, `cargo`, `cargo-audit` (via `cargo audit --version`), `actionlint`. Each is resolved via
`shutil.which`; found binaries also get a
best-effort version string (first line of `<tool> --version`, timeout-guarded).
`gh` additionally gets a `gh auth status` check so agents can tell "not
installed" apart from "installed but unauthenticated" before a
`dependabot`/`pr`-mode run fails partway through.
Doctor also warns when installed Cursor/Claude skill copies drift from the
bundled skill; JSON output includes these rows in the `skill_drift` array. Use
`repo-ops install-skill --check` for a read-only check, then
`repo-ops install-skill --force` to overwrite drifted copies.

- `--strict` — exit `1` when any probed binary is missing (default: exit `0`
  regardless of missing binaries).
- `--json` — emit a structured payload (`binaries[]`, `gh_auth`, `missing[]`,
  `found_count`, `total_count`); `--compact` for single-line output.
- `--json-out PATH` — write the report atomically to `PATH` (requires `--json`).

Run `repo-ops <subcommand> --help` for all flags.

### `install-skill` — bundled agent skill

`repo-ops install-skill --check` verifies installed Cursor/Claude skill copies without writing: exit `0` when absent or matching, `1` on drift; use `--force` to overwrite.

## Settings reference

One layered lookup per setting: **CLI flag** → **env var** → **config file** → **default**.

Config files (TOML):

- **Global:** `~/.config/repo-ops/config.toml` (or `$XDG_CONFIG_HOME/repo-ops/config.toml`)
- **Per-repo:** `[tool.repo-ops]` table in `pyproject.toml` at the repo root, or
  flat keys in `.repo-ops.toml` for repositories without a suitable pyproject

On Python 3.10, `repo-ops-core` depends on `tomli`. If neither stdlib `tomllib` nor `tomli` is available, config files are skipped with a warning.

For multi-root `plan` and `apply` runs, repository exclusions, Actions
lint/policy levels, Go upgrade mode, `go_work_sync`, `go_directive`, and
`go_toolchain` are resolved from each checkout's config; the launch checkout's
config is not inherited by the rest of the fleet. CLI flags and caller-provided
environment variables retain precedence. `timeout_check`, `timeout_apply`,
`timeout_verify`, `find_timeout`, and `git_timeout` are also resolved from each
checkout's config, as is `node_verify_script`.

Config scope is explicit:

- **Per-checkout:** `*_update_latest`, `*_targeted_bump`, and the
  config-backed refresh keys (`container_refresh_sha`, `precommit_refresh_sha`,
  and `submodule_refresh_sha`) are read from each checkout. The Deno
  `deno_update_latest` and `deno_targeted_bump` settings follow the same rule.
- **Invocation-scoped:** `jobs` and `yarn_max_concurrency` apply to the whole
  run. CLI flags and caller-provided environment variables override the
  corresponding config value for every checkout.

For `apply --commit=agent`, `REPO_OPS_COMMIT_AGENT_MODEL` or the
`commit_agent_model` config key selects the agent model; the default is
`composer-2.5`.

| Setting | Flag | Env var | Config key | Default |
| --------- | ------ | --------- | ------------ | --------- |
| jobs | `--jobs` | (none) | `jobs` | `min(16, cpu_count)` |
| yarn_max_concurrency | `--yarn-jobs` | `REPO_OPS_YARN_MAX_CONCURRENCY` | `yarn_max_concurrency` | `2` |
| go_upgrade_mode | `--go-upgrade-mode` | `REPO_OPS_GO_UPGRADE_MODE` | `go_upgrade_mode` | `patch` |
| actions_lint_level | `--actions-lint-level` | `REPO_OPS_ACTIONS_LINT_LEVEL` | `actions_lint_level` | `block` |
| timeout_check | (none) | `REPO_OPS_TIMEOUT_CHECK` | `timeout_check` | `60` |
| timeout_apply | (none) | `REPO_OPS_TIMEOUT_APPLY` | `timeout_apply` | `60` |
| timeout_verify | (none) | `REPO_OPS_TIMEOUT_VERIFY` | `timeout_verify` | `60` |
| find_timeout | (none) | `REPO_OPS_FIND_TIMEOUT` | `find_timeout` | `60` |
| git_timeout | (none) | `REPO_OPS_GIT_TIMEOUT` | `git_timeout` | `10` |
| bun_update_latest | `--latest` | `REPO_OPS_BUN_UPDATE_LATEST` | `bun_update_latest` | `0` |
| pnpm_update_latest | `--latest` | `REPO_OPS_PNPM_UPDATE_LATEST` | `pnpm_update_latest` | `0` |
| yarn_update_latest | `--latest` | `REPO_OPS_YARN_UPDATE_LATEST` | `yarn_update_latest` | `0` |
| deno_update_latest | `--latest` | `REPO_OPS_DENO_UPDATE_LATEST` | `deno_update_latest` | `0` |
| yarn_targeted_bump | `--yarn-targeted-bump` | `REPO_OPS_YARN_TARGETED_BUMP` | `yarn_targeted_bump` | `0` |
| npm_targeted_bump | `--npm-targeted-bump` | `REPO_OPS_NPM_TARGETED_BUMP` | `npm_targeted_bump` | `0` |
| pnpm_targeted_bump | `--pnpm-targeted-bump` | `REPO_OPS_PNPM_TARGETED_BUMP` | `pnpm_targeted_bump` | `0` |
| bun_targeted_bump | `--bun-targeted-bump` | `REPO_OPS_BUN_TARGETED_BUMP` | `bun_targeted_bump` | `0` |
| deno_targeted_bump | `--deno-targeted-bump` | `REPO_OPS_DENO_TARGETED_BUMP` | `deno_targeted_bump` | `0` |
| rust_targeted_bump | `--rust-targeted-bump` | `REPO_OPS_RUST_TARGETED_BUMP` | `rust_targeted_bump` | `0` |
| poetry_targeted_bump | `--poetry-targeted-bump` | `REPO_OPS_POETRY_TARGETED_BUMP` | `poetry_targeted_bump` | `0` |
| pdm_targeted_bump | `--pdm-targeted-bump` | `REPO_OPS_PDM_TARGETED_BUMP` | `pdm_targeted_bump` | `0` |
| pipenv_targeted_bump | `--pipenv-targeted-bump` | `REPO_OPS_PIPENV_TARGETED_BUMP` | `pipenv_targeted_bump` | `0` |
| pip_tools_targeted_bump | `--pip-tools-targeted-bump` | `REPO_OPS_PIP_TOOLS_TARGETED_BUMP` | `pip_tools_targeted_bump` | `0` |
| go_work_sync | (none) | `REPO_OPS_GO_WORK_SYNC` | `go_work_sync` | `0` |
| no_progress | (none) | `REPO_OPS_NO_PROGRESS` | `no_progress` | `0` |
| no_state | (none) | `REPO_OPS_NO_STATE` | `no_state` | `0` |
| no_lint_cache | (none) | `REPO_OPS_NO_LINT_CACHE` | `no_lint_cache` | `0` |
| go_directive | (none) | `REPO_OPS_GO_DIRECTIVE` | `go_directive` | (none) |
| go_toolchain | (none) | `REPO_OPS_GO_TOOLCHAIN` | `go_toolchain` | (none) |
| node_verify_script | (none) | `REPO_OPS_NODE_VERIFY_SCRIPT` | `node_verify_script` | (none) |
| commit_agent_provider | (none) | `REPO_OPS_COMMIT_AGENT_PROVIDER` | `commit_agent_provider` | `cursor` |
| commit_agent_model | (none) | `REPO_OPS_COMMIT_AGENT_MODEL` | `commit_agent_model` | `composer-2.5` |
| exclude_patterns | `--exclude` | (none) | `exclude` | `[]` |
| commit_umbrella | `--commit-umbrella` | `REPO_OPS_COMMIT_UMBRELLA` | `commit_umbrella` | `0` |
| actions_org_policy | `--actions-org-policy` | `REPO_OPS_ACTIONS_ORG_POLICY` | `actions_org_policy` | `0` |
| actions_policy_level | `--actions-policy-level` | `REPO_OPS_ACTIONS_POLICY_LEVEL` | `actions_policy_level` | `report` |
| container_update_latest | (none) | `REPO_OPS_CONTAINER_UPDATE_LATEST` | `container_update_latest` | `0` |
| container_refresh_sha | (none) | `REPO_OPS_CONTAINER_REFRESH_SHA` | `container_refresh_sha` | `0` |
| hatch_migrate_uv_lock | `--migrate-to-uv-lock` | `REPO_OPS_HATCH_MIGRATE_UV_LOCK` | `hatch_migrate_uv_lock` | `0` |
| precommit_update_latest | (none) | `REPO_OPS_PRECOMMIT_UPDATE_LATEST` | `precommit_update_latest` | `0` |
| precommit_refresh_sha | `--precommit-refresh-sha` | `REPO_OPS_PRECOMMIT_REFRESH_SHA` | `precommit_refresh_sha` | `0` |
| submodule_update_latest | (none) | `REPO_OPS_SUBMODULE_UPDATE_LATEST` | `submodule_update_latest` | `0` |
| submodule_refresh_sha | `--submodule-refresh-sha` | `REPO_OPS_SUBMODULE_REFRESH_SHA` | `submodule_refresh_sha` | `0` |
| resume | (none) | `REPO_OPS_RESUME` | `resume` | `0` |
| remediate_go_generate | (none) | `REPO_OPS_REMEDIATE_GO_GENERATE` | (none) | `0` |
| audit_fail_on | `--fail-on` | `REPO_OPS_AUDIT_FAIL_ON` | `audit_fail_on` | `high` |
| audit_fail_on_unknown | `--fail-on-unknown` | `REPO_OPS_AUDIT_FAIL_ON_UNKNOWN` | `audit_fail_on_unknown` | `0` |

## Package holds — capping a package's version temporarily

`exclude_patterns` above excludes whole repos; a **hold** does the same job
at package granularity — cap one package (e.g. `typescript`) at a maximum
version, optionally until an expiry date, without disabling `--latest` /
`--*-targeted-bump` for every other package in the repo.

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
ecosystem = "js"             # optional filter: js|npm|yarn|pnpm|bun|deno|go|rust|py|pdm|pipenv|pip-tools|poetry|hatch|precommit|actions|container|submodule
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

**Enforcement scope (this release):** JS (npm, yarn, pnpm, bun), Deno, Go,
Rust/Cargo, the Python managers (uv, hatch, pdm, pipenv, poetry, pip-tools),
pre-commit, GitHub Actions, containers, and submodules. `audit --fix`
advisory overrides take precedence over active holds for JS, Deno, Rust/Cargo,
and Python pin remediations, so a known-safe advisory target can lift a hold
for that remediation only.

**How it's enforced, per ecosystem:**

- **npm / yarn / pnpm / bun** — a held package's targeted/`--latest` bump
  targets the ceiling instead of `latest`, and a held package already
  resolved above its ceiling (e.g. a hold added after the fact) is clamped
  back down at the end of `apply` via a targeted install. Shows up in the
  existing `behind_latest` report bucket, not a new field.
- **deno** — `package` matches Deno outdated identities (`npm:…`, `jsr:…`).
  Outdated/planned and `behind_latest` targets above the ceiling are filtered;
  targeted bumps refuse to land above the ceiling; already-above-ceiling
  packages are clamped post-apply via selective `deno update` to a version
  within the ceiling; a prefix ceiling does not require its exact `.0` patch
  to exist. An advisory-safe version supplied by `audit --fix` overrides the
  hold for that remediation.
- **go** — Go's own resolver never crosses a major version boundary on its
  own (major ≥2 lives in the import path, e.g. `.../v2`), so a Go hold caps
  a same-major minor/patch version. Enforced the same way: filtered out of
  reported changes, and clamped back down post-apply via `go get
  <path>@<=ceiling>`.
- **rust** — `package` is the Cargo crate name. Planned `cargo update`
  bumps above the ceiling are filtered from check; post-apply clamp uses
  `cargo update -p <crate> --precise <version>` with the highest published
  registry version within a prefix ceiling. If no such version can be
  resolved, the clamp fails clearly and suggests an exact three-component
  `max_version`; it does not assume that `1.5.0` exists for a `1.5` ceiling.
  An advisory-safe version supplied by `audit --fix` overrides the hold for
  that crate and is used as the exact Cargo target.
- **uv / hatch / pdm / pipenv / poetry** — none of these has a per-package
  targeted install (`apply` regenerates the whole lockfile), so the
  post-apply clamp-down (read the lockfile, `<manager> add pkg<=ceiling` to
  correct) is the *only* enforcement mechanism here, not a safety net.
- **pip-tools** — compiles `requirements.txt` from a source manifest rather
  than an installed tree, so a hold is enforced by passing an ephemeral pip
  constraints file (`name<=ceiling` for held direct deps) to the compile
  step — `requirements.in` itself is never modified.
- **pre-commit** — `package` is the hook repo id (e.g. `example/hook`). Tag
  rev bumps and SHA-refresh targets whose resolved tag version exceeds a hold
  ceiling are filtered out of check and apply. Existing tag revs, and
  resolvable SHA revs, already above a ceiling are surfaced as actionable
  plan entries and clamped on apply to the highest available tag within the
  ceiling.
- **actions** — `package` is the action repo id (e.g. `actions/checkout`).
  Outbound tag bumps and SHA-refresh targets above the ceiling are filtered.
  Pins already above the ceiling (tag or resolvable SHA) are surfaced as
  actionable plan entries and clamped on apply to the highest available tag
  within the ceiling (SHA pins keep digest form when a matching tag exists).
  An active hold also blocks normalize-to-floating-`@vN` when the exact pin
  exceeds the ceiling. Holds do not change the default keep-SHA / unpin
  policy. Workspace-scoped holds use repo root (`.`); per-workflow path
  scoping is not supported.
- **container** — `package` is the image name without tag or digest. Hold
  lookup canonicalizes Docker Hub aliases, so `docker.io/library/node`
  matches `FROM node:…` / `library/node:…`. Tag bumps and tagged digest
  refreshes whose tag semver exceeds the ceiling are filtered; above-ceiling
  tagged refs are surfaced in plan and clamped on apply. SHA-only pins
  without tag intent stay report-only.
- **submodule** — `package` is the submodule path from `.gitmodules`. Tag
  advances and SHA-refresh targets above the ceiling are filtered.
  Above-ceiling tagged or resolvable SHA refs are surfaced as actionable
  plan entries and clamped on apply to the highest available tag within the
  ceiling.
