# Usage

Subcommands, JSON output, plan vs apply, security/discovery, ecosystem upgrade modes, Go modules, and Yarn isolated plan for the `repo-ops` CLI.

## Subcommands

Subcommands (entry point in `packages/repo-ops`):

| Subcommand | Purpose |
| ------------ | --------- |
| `inventory` | Read-only dependency footprint |
| `plan` | Outdated deps, Actions pin drift, remediations (no writes) |
| `apply` | Apply updates, Actions bumps, remediations, verify gates |
| `pushed` | Commits recorded by this machine's remote-tracking reflogs in a time window |
| `audit` | Fleet vulnerability sweep per ecosystem (read-only unless `--fix`) |
| `dependabot` | List/close Dependabot PRs superseded by local manifests |
| `sbom` | Emit CycloneDX/SPDX SBOM from lockfile dependencies (read-only) |
| `license` | Scan dependency licenses and flag policy violations (read-only) |
| `doctor` | Toolchain/auth preflight: probe known binaries and `gh` auth (read-only) |
| `install-skill` | Copy bundled agent skill into Cursor/Claude skill dirs |

Default scan roots: [`DEFAULT_ROOTS`](../packages/repo-ops-core/src/repo_ops_core/constants.py) (`/usr/local/src`, `~/Projects`, etc.). Pass explicit root directories to override. Discovery flags (`--flat`, `--follow-symlinks`, actions-only repos): [Security and discovery](#security-and-discovery).

`--json` is available on every subcommand except `sbom` (which emits its report via `--format`/`--output` instead). `--json-out PATH` (writes the report atomically to `PATH`; stdout prints only that path) is available on `inventory`, `plan`, `apply`, `pushed`, and `doctor` — for `plan`/`apply`, stdout is otherwise one JSON object. JSON reports include top-level `summary` counts (mirroring the human footer roll-up). Discovery notes live in `skipped` / `warnings` for JSON modes; human mode prints discovery lines to stderr. `--filter ECO` limits to one ecosystem: `node`, `go`, `rust`, `py`, `container`, `precommit`, `actions`, or `deno`. `--log-level {debug,info,warning,error}` sets the stdlib root logger level; `--verbose` / `-v` enables subprocess echo and implies `debug` when `--log-level` is absent. `--submodules` adds `submodules[]` to plan/apply JSON; `apply --submodule-order=children-first` processes child git repos before parent umbrellas when `.gitmodules` is present.

### `pushed` — machine-local reflog

`repo-ops pushed` counts only this machine's `refs/remotes/*` reflog entries
whose message is `update by push`. It does not query GitHub: a fresh clone, a
push made from another workstation, or a CI push can show zero here even when
GitHub records activity. Treat the result as local reflog evidence, not a
GitHub-wide pushed-commit audit.

### Plan vs apply

- **Plan** never mutates; exit code `1` when actionable drift exists (CI-friendly). Node **check** runs `corepack prepare` (via `check_yarn_snapshot`) so the correct Yarn version is activated in the temporary workspace — plan has a corepack side-effect and may access the network.
- **Apply** performs updates; `--no-verify`, `--skip-remediations`, or `--skip-actions` narrow scope. On verify failure, `--on-verify-fail=keep` (default) leaves the worktree as-is; `--on-verify-fail=revert` restores pre-apply lockfile snapshots and git-tracked paths under the workspace. Human and JSON workspace rows include `verify_fail_disposition` (`FAILED_WITH_CHANGES` vs `FAILED_CLEAN`) when verify fails.
- **Verification tests:** `--verify-test` runs the additional Go and Rust test gates during apply verification. Go tests run with the Go verify gates, and Rust runs `cargo test` after `cargo check`; the default remains off.
- Actions pins included by default; `--skip-actions` for dependency-only runs.
- **Cross-major Actions bumps:** By default a newer major upstream (e.g. `actions/checkout@v4` → `v6`) is **report only**. `--actions-upgrade-mode=latest` (or the `--latest` umbrella) applies it: tag-style `major` rewrites to the floating `@v{max}` (only when a bare `v{max}` tag exists upstream — same safety check as normalize), tag-style `exact` rewrites to the highest `@v{max}.M.P`. When the newest major cannot be safely targeted, it falls back to normalize/report-only. An explicit `--actions-upgrade-mode` overrides the `--latest` umbrella. For SHA-pinned actions, combine `--actions-refresh-sha` with `--latest` to re-pin to the newest major's commit SHA (with comment update) — see SHA digest refresh below.
- **SHA pins (default: preserve):** By default, `uses: owner/repo@<sha>` pins are **report only** on apply (supply-chain hardening). Opt in to tag resolution with `--actions-unpin-sha`. When unpinning: (1) trailing `# vN[.N.N]` comment → use that major; (2) tags API SHA→tag lookup → highest semver tag for that commit. If neither resolves, the SHA is reported but left untouched. `--actions-tag-style=major` (default) → `@vN`; `exact` → highest `@vN.M.P` within the resolved major (falls back to `@vN`).
- **SHA digest refresh (keep pinned):** `--actions-refresh-sha` keeps a `uses: owner/repo@<sha>  # vN` action pinned **by digest** but advances the SHA to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` comment. Use this to supersede Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. Combined with `--latest` (or `--actions-upgrade-mode=latest`), also **crosses major boundaries**: re-pins to the newest major's commit SHA and updates the `# vN` comment to the new major (e.g. `# v6` → `# v7`) — superseding Dependabot's cross-major SHA-pin PRs such as `actions/checkout` v6→v7 or `sigstore/cosign-installer` v3→v4. Falls back to within-major refresh when the newest major has no exact `vN.M.P` tag. The major is resolved from the `# vN` comment, else the tags API. No effect with `--actions-unpin-sha` (that resolves the SHA to a tag instead); a no-op when the major can't be resolved or the pin is already the newest digest.
- **Full coverage (`--full`):** This opt-in umbrella enables `--latest`, `--actions-refresh-sha`, `--container-refresh-sha`, and all four JavaScript targeted-bump flags (`--yarn-targeted-bump`, `--npm-targeted-bump`, `--pnpm-targeted-bump`, and `--bun-targeted-bump`). It does not change defaults when absent. `--latest` alone is incomplete: it does not refresh SHA-pinned Actions or container digests and does not close JavaScript `behind_latest` gaps.
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
`summary.excluded`; dependabot reports them in `summary.excluded_repos`.

### Ecosystem upgrade modes (plan and apply)

| Ecosystem | Default behavior | Aggressive / latest opt-in |
| ----------- | ------------------ | ---------------------------- |
| **npm** | `npm update` (semver ranges in manifests) | Add `--npm-targeted-bump` (`REPO_OPS_NPM_TARGETED_BUMP=1`) to move deps left behind when a latest-mode bump was attempted |
| **pnpm** | `pnpm update -r` (within ranges) | `REPO_OPS_PNPM_UPDATE_LATEST=1` → adds `--latest`; add `--pnpm-targeted-bump` (`REPO_OPS_PNPM_TARGETED_BUMP=1`) for behind-latest fallback |
| **yarn** | `yarn up` (Berry, in-range bumps in isolated plan snapshots) | `REPO_OPS_YARN_UPDATE_LATEST=1` → `yarn up -R '*'`; reports direct deps yarn's resolver kept behind their newest published version as `behind_latest` (#110). Add `--yarn-targeted-bump` (`REPO_OPS_YARN_TARGETED_BUMP=1`) to also *move* those gaps: a reversible per-package `yarn up <pkg>@latest`, kept only if the workspace still installs and the version actually advances (#111) |
| **bun** | `bun update` (within ranges) | `REPO_OPS_BUN_UPDATE_LATEST=1` → includes out-of-range "latest" packages. Add `--bun-targeted-bump` (`REPO_OPS_BUN_TARGETED_BUMP=1`) to also *move* deps `bun update --latest` left behind: a reversible per-package `bun add <pkg>@latest`, kept only if the workspace still installs and the version actually advances |
| **go** | `REPO_OPS_GO_UPGRADE_MODE=patch` (default) | `explicit` or `mvs` (see below) |
| **rust** | `cargo update --dry-run` / `cargo update` (within semver constraints in `Cargo.lock`) | No cross-range / `--latest` mode — `--latest` does not change cargo behavior |
| **python** | Manager-specific (`uv`, poetry, pip-tools, hatch) | See each plugin's check/apply |
| **pdm** | `pdm update --dry-run` / `pdm update` (in-range); skipped with a WARN when `pdm` is not on PATH | — |
| **pipenv** | venv `pip list --outdated --format=json` (via `pipenv --venv`, read-only; never creates a venv) / `pipenv update`; skipped with a WARN when `pipenv` is not on PATH or the venv is not initialised | — |
| **deno** | `deno outdated` / `deno outdated --update` (Deno ≥ 2.x); supports both box-drawing and plain-text output; skipped with a WARN when `deno` is not on PATH | — |
| **pre-commit** | Checks `.pre-commit-config.yaml` hook revs against latest upstream tags (within-major only by default) | `REPO_OPS_PRECOMMIT_UPDATE_LATEST=1` → allow cross-major rev bumps |
| **GitHub Actions** | `--actions-upgrade-mode=normalize` (default): normalize exact tag pins to the floating major (`@vN`); a newer major upstream is reported only. SHA pins: report only (preserve digest). | `--actions-upgrade-mode=latest` (or `--latest`) → also apply cross-major bumps; `--actions-unpin-sha` → resolve SHA pins to tags; `--actions-refresh-sha` → advance digest within current major (with `--latest`: cross-major re-pin + comment update); `--actions-tag-style=exact` → highest `@vN.M.P` when unpinning |
| **container** | Checks `FROM` / `image:` references in Dockerfiles and Compose files; SHA-pinned images are report-only by default | `REPO_OPS_CONTAINER_UPDATE_LATEST=1` (or `--latest`) → allow cross-major tag bumps; `REPO_OPS_CONTAINER_REFRESH_SHA=1` → advance SHA digest for `image:tag@sha256:…` pins to the latest digest for the same tag (keeps pin hardened, advances to current content) |

#### Hatch and `--migrate-to-uv-lock`

Hatch workspaces without a `uv.lock` are not upgraded unless `--migrate-to-uv-lock` (or `REPO_OPS_HATCH_MIGRATE_UV_LOCK=1`) opts in to creating one. On apply **verify**, when hatch has no `uv.lock`, verify intentionally skips `uv sync --locked` (nothing to lock against until migration) and runs only hatch fast gates (`check`/`typecheck` scripts, ruff, mypy, and pyrefly when configured). When `[tool.pyrefly]` configures `project-excludes` or `project-includes`, verify passes those paths as pyrefly CLI flags so the project-defined scope is honored without hardcoded exclusions.

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

A `resolutions`/`overrides`/`pnpm.overrides` manifest pin blocking the target is not automatically a hard stop: if no [package hold](#package-holds--capping-a-packages-version-temporarily) covers the package, the retry releases that pin first (it predates the hold feature, or was a one-off fix never promoted to a tracked hold) before running the install — reverted along with everything else in the snapshot if the retry still doesn't land. A package covered by an active hold keeps its pin untouched and is bumped only up to the hold's ceiling. This applies to the *initial* bulk-update ghost-bump guard too (yarn/bun): with targeted-bump enabled, a blocked target from that first pass is handed to this retry instead of failing the whole workspace outright — the guard only raises immediately when there is no targeted-bump fallback to hand it to.

### Submodule tag-advance

When `--submodules` is active, `plan` detects available semver tag advances for each discovered submodule (comparing the current registered commit against upstream tags). By default only **within-major** advances are surfaced. Set `REPO_OPS_SUBMODULE_UPDATE_LATEST=1` to also surface and apply **cross-major** tag advances. `apply --submodule-order=children-first` applies submodule pointer updates before parent umbrella commits.

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
| `py-uv` | `uv` + `pip-audit` | `uv run pip-audit --format json`; skipped when pip-audit not importable |
| `rust` | `cargo` + `cargo-audit` | `cargo audit --json`; skipped if `cargo audit --version` fails |

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

Scaffold detection covers npm, Bun lockfiles (`package-ecosystem: bun`), Deno,
pre-commit, pip, Go modules, Cargo, Docker, and GitHub Actions. This is separate
from supersession parsing, which determines whether existing Dependabot PRs are
already satisfied by local manifests or lockfiles.

`--json` and `--close` are invalid with `--scaffold`.

Precision caveats:

- **High-confidence:** lockfile parsers resolve npm/yarn/pnpm/bun, `uv.lock`,
  `poetry.lock`, `pdm.lock`, and `Pipfile.lock` versions; go.mod `require`
  blocks and Cargo.lock `[[package]]` entries are parsed structurally. These
  Python lockfile matches can qualify Dependabot PRs for `--close`; grouped
  PRs still require every row to be high-confidence and satisfied.
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
discovered lockfile dependencies (read-only; no network calls).

- `--format cyclonedx|spdx` — output format (default: `cyclonedx`).
- `--output PATH` — write the SBOM to `PATH` instead of stdout.
- `--compact` — emit single-line JSON instead of pretty-printed.

### `license` — dependency license policy scan

`repo-ops license [root_dir ...]` classifies dependency licenses and flags
policy violations (read-only; no network calls).

- `--allow LIST` — comma-separated SPDX identifiers that are permitted; any
  license not in this list is flagged.
- `--deny LIST` — comma-separated SPDX identifiers that are forbidden; any
  license in this list is flagged.
- `--no-fail-unknown` — exit `0` even when unknown licenses are found
  (default: exit `2` when unknown licenses exist in ecosystems with a local extractor).
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
| Deno | `deno.lock` npm specifiers via DENO_DIR npm cache |
| container | no local license source (always unknown; does not trigger exit `2`) |

Poetry, PDM, and pipenv lockfiles without an installed venv may report unknown
for Python packages. See `repo-ops license --help` epilog for SPDX compound-expression handling.

### `doctor` — toolchain/auth preflight

`repo-ops doctor` probes the binaries repo-ops shells out to across all
ecosystems (read-only; no scan roots, no `root_dir` arguments): `git`, `gh`,
`uv`, `pip-compile`, `poetry`, `pipenv`, `pdm`, `pip-audit`, `pyrefly`, `hatch`,
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
- **Per-repo:** `[tool.repo-ops]` table in `pyproject.toml` at the repo root

Python 3.11+ only; skipped with a warning on 3.10 unless `tomli` is installed.

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
| timeout_apply | (none) | `REPO_OPS_TIMEOUT_APPLY` | `timeout_apply` | `300` |
| timeout_verify | (none) | `REPO_OPS_TIMEOUT_VERIFY` | `timeout_verify` | `120` |
| find_timeout | (none) | `REPO_OPS_FIND_TIMEOUT` | `find_timeout` | `60` |
| git_timeout | (none) | `REPO_OPS_GIT_TIMEOUT` | `git_timeout` | `10` |
| bun_update_latest | `--latest` | `REPO_OPS_BUN_UPDATE_LATEST` | `bun_update_latest` | `0` |
| pnpm_update_latest | `--latest` | `REPO_OPS_PNPM_UPDATE_LATEST` | `pnpm_update_latest` | `0` |
| yarn_update_latest | `--latest` | `REPO_OPS_YARN_UPDATE_LATEST` | `yarn_update_latest` | `0` |
| yarn_targeted_bump | `--yarn-targeted-bump` | `REPO_OPS_YARN_TARGETED_BUMP` | `yarn_targeted_bump` | `0` |
| npm_targeted_bump | `--npm-targeted-bump` | `REPO_OPS_NPM_TARGETED_BUMP` | `npm_targeted_bump` | `0` |
| pnpm_targeted_bump | `--pnpm-targeted-bump` | `REPO_OPS_PNPM_TARGETED_BUMP` | `pnpm_targeted_bump` | `0` |
| bun_targeted_bump | `--bun-targeted-bump` | `REPO_OPS_BUN_TARGETED_BUMP` | `bun_targeted_bump` | `0` |
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
| container_update_latest | (none) | `REPO_OPS_CONTAINER_UPDATE_LATEST` | `container_update_latest` | `0` |
| container_refresh_sha | (none) | `REPO_OPS_CONTAINER_REFRESH_SHA` | `container_refresh_sha` | `0` |
| hatch_migrate_uv_lock | `--migrate-to-uv-lock` | `REPO_OPS_HATCH_MIGRATE_UV_LOCK` | `hatch_migrate_uv_lock` | `0` |
| precommit_update_latest | (none) | `REPO_OPS_PRECOMMIT_UPDATE_LATEST` | `precommit_update_latest` | `0` |
| submodule_update_latest | (none) | `REPO_OPS_SUBMODULE_UPDATE_LATEST` | `submodule_update_latest` | `0` |
| resume | (none) | `REPO_OPS_RESUME` | `resume` | `0` |
| remediate_go_generate | (none) | `REPO_OPS_REMEDIATE_GO_GENERATE` | `remediate_go_generate` | `0` |
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
ecosystem = "js"             # optional filter: js|npm|yarn|pnpm|bun|go|py|pdm|pipenv|pip-tools|poetry|hatch
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

**Enforcement scope (this release):** JS (npm, yarn, pnpm, bun), Go, and the
Python managers (uv, hatch, pdm, pipenv, poetry, pip-tools). Not yet wired:
containers, GitHub Actions, submodules, pre-commit — same rule schema, just
not consulted by those checkers yet.

**How it's enforced, per ecosystem:**

- **npm / yarn / pnpm / bun** — a held package's targeted/`--latest` bump
  targets the ceiling instead of `latest`, and a held package already
  resolved above its ceiling (e.g. a hold added after the fact) is clamped
  back down at the end of `apply` via a targeted install. Shows up in the
  existing `behind_latest` report bucket, not a new field.
- **go** — Go's own resolver never crosses a major version boundary on its
  own (major ≥2 lives in the import path, e.g. `.../v2`), so a Go hold caps
  a same-major minor/patch version. Enforced the same way: filtered out of
  reported changes, and clamped back down post-apply via `go get
  <path>@<=ceiling>`.
- **uv / hatch / pdm / pipenv / poetry** — none of these has a per-package
  targeted install (`apply` regenerates the whole lockfile), so the
  post-apply clamp-down (read the lockfile, `<manager> add pkg<=ceiling` to
  correct) is the *only* enforcement mechanism here, not a safety net.
- **pip-tools** — compiles `requirements.txt` from a source manifest rather
  than an installed tree, so a hold is enforced by passing an ephemeral pip
  constraints file (`name<=ceiling` for held direct deps) to the compile
  step — `requirements.in` itself is never modified.
