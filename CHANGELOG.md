# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **yarn conservative mode actually upgrades now:** default (non-latest) mode ran bare `yarn up`, which in Yarn 4 resolves and installs but upgrades **nothing** — every default-mode run reported `0 upgrade(s)` on yarn workspaces while plain in-range bumps (e.g. `@biomejs/biome ^2.4.14 → 2.4.16`) sat unapplied and Dependabot kept filing PRs repo-ops should have superseded. Conservative mode now runs `yarn up -R '*'` (re-resolve all resolutions within existing package.json ranges, ranges untouched); latest mode keeps behind-latest reporting (#110) and targeted range-breaking bumps (#111) on top. (#114)
- **Actions: exact tag pins advance within their major when upstream has no floating `vN` tag:** `astral-sh/setup-uv@v8.1.0` (only `vN.M.P` tags published) could never move — normalize had no bare major to collapse to and the report-only row was hidden from the report (`pin == latest`). Such pins now bump to the highest exact tag within their current major (tag-pin analog of #109's SHA digest refresh). (#115)

- **Go ghost upgrades eliminated (check + post-apply):** `go list -m -u all` enumerates the full module graph, including modules no package of the build needs (`stretchr/objx`, `kr/pty`, `cel.dev/expr`, …); upgrading those is immediately undone by `go mod tidy`, yet they were counted and printed as upgrades — a run reported "50 package(s) upgraded" with every worktree byte-identical afterward. Go check/plan and explicit-mode `go get` targets are now filtered to modules recorded in the workspace `go.mod` (`go mod edit -json` Require list, fail-open when unreadable), and the post-apply fallback that re-ran the ecosystem checker — reporting *still-available* updates as *applied* changes whenever the lockfile diff was empty — is removed: a clean diff now reports zero changes for every ecosystem. (#113)
- **Report-only Actions rows no longer print `[ok]`:** rows `apply` never rewrites (newer major in normalize mode, SHA-pinned report-only, no floating major upstream) printed `[ok]`, reading as an applied bump (e.g. `codecov-action@v6 → v7 [ok]` while the workflow still pinned v6). They now print `[report-only]`. (#113)

## [0.12.0] - 2026-06-07

### Added

- **`--yarn-targeted-bump`: move the `behind_latest` gap, not just report it (option 2 of #110):** with `--latest`, for each direct dependency that `yarn up -R '*'` left behind (peer/group constraints, surfaced as `behind_latest` in #110), repo-ops can now attempt a reversible targeted `yarn up <pkg>@latest`. It snapshots the lockfile/manifests, runs the targeted up plus `yarn install`, and **keeps the result only if install succeeds and the resolved version actually advances** — otherwise it restores the snapshot and the dependency stays `behind_latest`, so nothing broken is committed. The version-advance gate also drops the ghost-bump case where the lock churns but the resolver holds the old version. Opt-in via the flag or `REPO_OPS_YARN_TARGETED_BUMP`; successful bumps appear as normal planned changes, so `plan` previews exactly what `apply` lands. (#111)

## [0.11.0] - 2026-06-05

### Added

- **yarn latest-mode surfaces packages left behind:** after `yarn up -R '*'` (`--latest`), repo-ops now diffs each direct dependency's resolved version against the latest published version and reports any that yarn's resolver kept behind (peer/group constraints) as `behind_latest`, instead of silently omitting them. A supersede run no longer reads as "everything is current" when a newer version exists but wasn't applied (e.g. `@sentry/nextjs` `10.55.0` while `10.56.0` is published). Only runs in latest mode; a registry lookup failure skips that package rather than failing the run. (#110)
- **`--actions-refresh-sha` flag:** keeps a SHA-pinned action pinned **by digest** but advances the SHA to the commit of the newest `vN.M.P` release **within its current major**, preserving the `# vN` audit comment. Lets repo-ops supersede Dependabot's digest-pin bumps (e.g. `actions/checkout` `6.0.2 → 6.0.3`) without un-hardening the pin to a floating tag. The major is resolved from the `# vN` comment, else the tags API; no effect with `--actions-unpin-sha`. (#109)
- **Subcommand `--help` shows full flags:** the top-level dispatcher delegates `repo-ops plan --help` / `apply --help` (etc.) to the real subcommand parsers so `--latest`, `root_dir`, exit-code epilogs, and DEFAULT_ROOTS paths appear without reading source.
- **AGENTS.md quick reference:** invocations, DEFAULT_ROOTS, `--latest` umbrella, workflow, flags cheat sheet, and explicit do-not-explore guidance for routine maintenance runs.

### Fixed

- **Actions-only repos now commit under `--commit`:** `_bundle_apply_ok` bailed with "no workspaces" before considering action bumps, so a repo with workflows but no package manifest applied action bumps to the worktree but never committed them. (#107)
- **A dirty sibling no longer blocks committing a clean workspace's bump:** the post-apply dirty guard was repo-wide (`any(started_dirty)`, itself repo-wide via `is_git_dirty`), so under `--jobs` one dirty/skipped workspace dropped every other workspace's commit. A dirty-start repo now scopes the commit to the upgrade's own paths instead of skipping wholesale; a root-workspace manifest (e.g. `package.json`) is no longer dropped from that scoped commit. (#108)

### Changed

- **`plan`/`apply` `--help` text:** richer `--latest`, `--allow-dirty`, `--jobs`, and DEFAULT_ROOTS help; plan/apply epilogs document exit codes and agent vs human docs.
- **Bundled agent skill:** points to AGENTS.md instead of duplicating flag tables; instructs agents to run named commands directly.

### Documentation

- **Doc tiers:** README/HUMANS/CONTRIBUTING/usage aligned on agent quick reference vs `--help` as canonical flags; SHA-pin default (preserve digest) clarified in usage.md.

## [0.10.0] - 2026-06-05

### Added

- **`--actions-upgrade-mode {normalize,latest}` flag:** controls how the GitHub Actions upgrader treats a pin whose major is behind the newest upstream major line, mirroring `--go-upgrade-mode`. `normalize` (default) keeps the prior behavior — collapse exact tag pins to the floating major and report newer majors as report-only. `latest` **applies** cross-major bumps to the newest available major, honoring `--actions-tag-style` (`major` → floating `@vN`; `exact` → highest `@vN.M.P`) and the existing bare-major safety check; it falls back to normalize/report-only when the newest major cannot be safely targeted. (#106)

### Changed

- **`--latest` now also applies cross-major Action bumps:** the `--latest` umbrella opts GitHub Actions into `latest` mode (equivalent to `--actions-upgrade-mode=latest`), closing the gap where node ecosystems honored `--latest` but Actions cross-major bumps were always report-only. An explicit `--actions-upgrade-mode` overrides the umbrella. Without either opt-in, cross-major Action bumps remain report-only. (#106)
- **Behind-latest footer wording:** the Actions "newer major available" footer hint now points operators at the apply path (`--actions-upgrade-mode=latest` / `--latest`); applied cross-major bumps are counted under "actions bumps" while unresolvable ones remain under "behind-latest". (#106)

## [0.9.0] - 2026-06-04

### Added

- **`--latest` flag:** a single `--latest` flag on `plan`/`apply` enables aggressive (include-majors) upgrades across every ecosystem, replacing the four undiscoverable per-ecosystem env vars (`REPO_OPS_BUN_UPDATE_LATEST`, `REPO_OPS_PNPM_UPDATE_LATEST`, `REPO_OPS_YARN_UPDATE_LATEST`, `REPO_OPS_GO_UPGRADE_MODE`); those env vars are still honored. (#102)
- **`--go-upgrade-mode {patch,explicit,mvs}` flag:** the Go upgrade mode — previously only the `REPO_OPS_GO_UPGRADE_MODE` env var, documented solely in HUMANS.md — is now a first-class CLI flag surfaced in `--help`. (#102)
- **`--json-out PATH` for `pushed` and `inventory`:** both commands can now write their JSON report atomically to a file (via a new `write_json_atomic` core helper) instead of only streaming to stdout, matching `plan`/`apply`. (#104)
- **`--top N` for `inventory`:** caps the displayed rows — the TOTAL is still computed over the full set and "… and N more" is appended; `--json` still emits every row. (#104)
- **`--jobs N` for `pushed` and `inventory`:** the worker-pool size is now configurable (previously hardcoded to `min(16, cpu)` and `8` respectively), matching `plan`/`apply`. (#104)
- **`--manager` filter:** scope a run to a single package manager (bun/npm/pnpm/yarn/uv/poetry/pip-tools/hatch/go/rust), complementing the ecosystem `--filter` for mixed monorepos. (#104)
- **`--version` flag:** `repo-ops --version` now prints the installed version instead of erroring on the required subcommand. (#104)
- **`--only-outdated` for `apply`:** the triage flag (previously plan-only) now also works on `apply`. (#103)
- **`--compact` flag:** emits single-line (unindented) JSON across all three producers for NDJSON-adjacent piping. (#101)
- **`schema_version: 1`** is now the first key in every JSON payload (plan/apply, pushed, inventory), giving consumers a migration gate. (#101)
- **"packages upgraded" footer total:** the run footer (and JSON `summary`) now reports the total package-upgrade count across workspaces. (#103)

### Changed

- **Per-ecosystem behind-latest hints:** the human-report footer now gives each manager its correct "include latest" remediation (bun/pnpm/yarn → `--latest`; npm → "no latest mode; bump the pin manually") instead of the one-size-fits-all `REPO_OPS_BUN_UPDATE_LATEST=1` advice, which was wrong for npm/pnpm. (#102)
- **Triage-sorted report under `--only-outdated`:** repos now sort heaviest-first (by upgrade count) when `--only-outdated` is set; full reports stay alphabetical. (#103)
- **Aligned change rows** (`name  from → to` via `table.py`) and **suppressed empty `## repo` headers** under `--only-outdated --skip-actions` in the human report. (#103)
- **JSON payloads omit null fields:** `error`, `verify_error`, and `detail` are now omitted when null (matching the existing omit-when-None fields), shrinking large payloads. (#101)
- **Harmonized JSON roots key:** `inventory` now emits `roots` (was `scanned_roots`), consistent with plan/apply and pushed. (#101)

### Fixed

- **`set_version.py` show mode** now exits 1 when any manifest version is unparseable, completing the exit-code contract #98 added to the set/bump paths (the show path silently exited 0). (#100)

### Documentation

- Documented the `REPO_OPS_BUN_UPDATE_LATEST` and `REPO_OPS_FIND_TIMEOUT` environment variables, and removed the pre-1.0 0.2.x `repos-*` migration guide (no migration guide needed for a pre-1.0 project).
- Refocused `HUMANS.md` and `AGENTS.md` as concise entry docs and moved the deep reference (installation, usage, bulk maintenance, programmatic imports, testing, architecture) into a new `docs/` canon directory linked from each.

## [0.8.0] - 2026-06-04

### Added

- **CI dependency audit:** a dedicated `audit` job runs `pip-audit` on every push/PR, scanning the resolved third-party dependency tree for known CVEs (`--skip-editable` drops the local workspace packages).

### Changed

- **Expanded lint coverage:** ruff now enforces flake8-bandit (`S`), flake8-simplify (`SIM`), flake8-comprehensions (`C4`), and ruff-native (`RUF`) rules on top of `E/F/I/UP/B`. `S603`/`S607` are ignored project-wide (invoking package managers by name off `PATH` is repo-ops's purpose, not a vulnerability) and tests ignore `S101`/`S108`; the remaining best-effort exception swallows are annotated. The `scripts/` directory is now linted and formatted alongside the packages.

### Fixed

- **Actions pin update (read-only / vanished workflow file):** `update_workflow_pins` now wraps its YAML load *and* write in `OSError` handling, raising `WorkflowParseError` like `parse_workflow_uses` already did, so a read-only or mid-scan-deleted `.github/workflows/*.yml` is recorded as a failed bump instead of an uncaught `PermissionError`/`OSError` aborting the entire actions run. (#77)
- **Override audit (range/caret pins):** `overrides_audit` no longer fires a false `OVERRIDE stale` for range pins (`^1.2.3`, `~`, `>=`). The string-equality stale check is now gated to exact semver pins, so an in-range higher resolved version (e.g. `1.2.5` satisfying `^1.2.3`) passes silently while the floor comparison still flags genuine downgrades. (#78)
- **Lockfile version parsers (bogus tokens):** the pnpm packages-section parser strips the parenthesized peer-dep suffix (`@babel/core@7.21.0(supports-color@8.1.1)` → `@babel/core@7.21.0`) and the yarn-Berry parser skips the `__metadata` preamble block, so the inventory `versions` set no longer carries garbled pnpm-v6 tokens or a bogus `__metadata@6` token per Berry lockfile. (#79)
- **Commit classifier (`bun.lock`):** `classify_commit_subject` now recognizes Bun ≥1.0's text `bun.lock` (not just the legacy binary `bun.lockb`) as a Node lockfile, so a lockfile-only Bun update gets the "update Node lockfiles and manifests" subject instead of the generic dependency-update fallback. (#80)
- **Override resolution (bare-scope key):** `resolved_version_from_node_modules` no longer crashes with `ValueError` on a slashless scoped override key (e.g. `@types`); the scoped-split branch now requires a `/`, so a single malformed override key degrades to a warning instead of failing the entire workspace check. (#81)
- **Post-apply package.json diff (non-dependency fields):** `collect_changes_after_apply` now tracks which JSON section each diff line is in and only emits changes from dependency sections, so a bumped package `"version"`/`"name"`/`"license"`/script line is no longer captured as a spurious `Change` row polluting remediation context and the commit body. (#82)
- **Post-apply lightweight path (root workspaces):** the fast `git diff --name-only` path now skips its workspace-prefix filter when the workspace is the git root (`rel_s == "."`), so root-workspace applies use the lightweight path as intended instead of always falling back to a full checker re-run. (#83)
- **Commit-agent draft failures (transport errors):** `plan_repo_commit` now catches any `Exception` (not only `RuntimeError`) from the optional cursor commit-agent draft, so a network/transport error (`ConnectionError`/`TimeoutError`) during `--commit=agent` degrades to a skipped commit with a reason instead of crashing the commit phase after a successful apply. (#84)
- **HUMANS.md (plan-phase Corepack):** corrected the stale "Node check does not run Corepack" note — the yarn snapshot check runs `corepack prepare` during plan, so plan has a corepack side-effect and may access the network. (#85)
- **Reconciliation dict (loop-invariant):** `run.py` builds the `path_to_result` map once before the bundle loop instead of rebuilding it per bundle, removing the O(bundles × results) rework on large multi-repo scans (output unchanged). (#86)
- **requirements.txt parser (extras & inline markers):** `parse_requirements_lines` now matches an optional `[extras]` group (keyed by the bare package name) so `requests[security]==2.31.0` is no longer dropped from the change diff, and stops the version capture at whitespace or `;` so an inline marker (`flask==3.0.0;sys_platform=="win32"`) no longer pollutes the captured version. (#87)
- **`--top` validation (negative counts):** `--top` now uses the `positive_int` validator (matching `--jobs`), rejecting negative values that previously corrupted the per-workspace change slice and inflated the "… and N more" count; the same unguarded `--top` in `repo-ops-pushed audit` is fixed too. (#88)
- **Post-apply fallback (dropped lockfiles):** `collect_changes_after_apply` now derives lockfile names from the manager constants instead of an `.endswith(".lock")` heuristic, so the fallback path no longer drops `package-lock.json`, `pnpm-lock.yaml`, and `bun.lockb` (`.json`/`.yaml`/`.lockb` suffixes) from its change list — matching the primary snapshot path. (#89)
- **bun checker timeout:** `check_bun` now passes `timeout=TIMEOUT_CHECK` (600 s) to both `bun outdated` calls instead of the 300 s default — the bun sibling of #57/#64/#70. (#90)
- **pnpm `--latest` plan/apply parity:** `check_pnpm` now honors `REPO_OPS_PNPM_UPDATE_LATEST` (same truthiness as bun), so packages that `apply_pnpm --latest` will upgrade are reported in `changes` instead of the non-actionable `behind_latest` bucket. (#91)
- **Pushed counts (intervening fetch):** `pushed` now resolves each push entry's base via `_reflog_pre_sha` (full-reflog walk) rather than the previous push's tip, so a `git fetch` between two pushes no longer attributes the fetched colleague commits to the later push. (#92)
- **hatch venv checker timeout:** the no-`uv.lock` venv branch of `check_hatch` now passes `timeout=TIMEOUT_CHECK` (600 s) to its `uv pip install -e` call instead of a hardcoded 300 s — completing the #70 fix for the venv path. (#93)
- **Discovery (unreadable subdir):** `discover_repos` now processes `find`'s stdout even on a non-zero exit (one permission-denied subdir no longer silently drops every repo under that root) and warns to stderr — mirroring the timeout branch. (#94)
- **Discovery (vanished repo dir):** the `git_common_dir` call site now also catches `FileNotFoundError`/`OSError` (warn + skip), so a repo directory that disappears mid-scan no longer aborts the whole discovery — the sibling of #71. (#95)
- **Inventory edge count (vendored deps):** `count_dep_edges` now prunes with the shared `SKIP_DIRS` constant instead of an incomplete hardcoded set, so vendored manifests under `vendor/`, `third_party/`, `.yarn/`, etc. no longer inflate the `edges` column out of sync with the other inventory columns — completing #75. (#96)
- **Import-boundary check (dotless imports):** `check_import_boundaries` now applies the submodule allowlist to the dotless `from repo_ops_upgrade import X` form (collecting `node.names`), not just the dotted form, so a disallowed cross-package import is actually flagged. (#97)
- **`set_version` skipped manifests:** `set_version.py` now warns to stderr, counts the skip, and exits non-zero when a manifest's version cannot be parsed (single-quoted, PEP 440 pre/post-release) instead of silently dropping it and reporting success — surfacing the mixed-version drift it exists to prevent. (#98)
- **pip-tools verify gate:** `verify_workspace` now has a real `pip-tools` branch (recompile-and-compare against `requirements.in`) instead of falling through to `return None`, so `--verify` no longer reports a silent false pass for every pip-tools workspace. (#99)

## [0.7.1] - 2026-06-04

### Fixed

- **Workspace-member matching (sibling bleed):** uv/yarn/npm and Cargo workspace-member matching is now separator-bound, so a member pattern targeting `packages/foo` no longer also claims the sibling `packages/foobar`, and a bare `apps` pattern no longer claims `apps-legacy`. Previously a `startswith` prefix test with no path-boundary check silently folded sibling workspaces into a member and dropped them from the outdated scan. Fixed in place in all three checkers (`path_is_uv_workspace_child`, `path_is_yarn_workspace_child`, the upgrade `discovery` matcher). (#59)
- **Version normalization (operator whitespace + build metadata):** `normalize_version` now strips whitespace left behind by a range operator (`>= 1.2.3` → `1.2.3`, previously yielding a `(0, 2, 3)` tuple) and drops `+build` metadata (`1.2.3+build.1` → `1.2.3`, previously `(1, 2, 3, 1)`). Leading `v`/`V` and `-prerelease` stripping are unchanged. (#60)
- **Actions SHA-unpin (short pins):** `--actions-unpin-sha` now resolves short (≤7-char) SHA pins back to a tag by a unique-prefix scan of the full-SHA → tags map. The previous exact-key lookup only matched full 40-char SHAs, so short-SHA pins never resolved. Ambiguous prefixes are left unresolved. (#61)
- **Worker exceptions no longer escape as a clean exit 0:** An exception raised inside a per-workspace upgrade worker (`run_remediations` / `verify_workspace`) is now recorded as a `status: fail` workspace result with the error text, yielding a non-zero exit. Previously the bounded thread pool swallowed the exception and the run reconciliation re-emitted the workspace as its original (clean) result, so a crashed workspace was reported clean with exit 0. (#62)
- **Post-apply diff scan (phantom downgrades):** The `package.json` post-apply diff scan now matches added (`+`) lines only, instead of both `+` and `-`. Capturing removed lines recorded the old version as a spurious downgrade change alongside the real upgrade. (#63)
- **Checker timeouts (uv, Rust):** `check_uv` and `check_rust` now pass `TIMEOUT_CHECK` (600 s) to their dry-run subprocesses instead of falling back to the 300 s default — the sibling fix to #57 for npm/go. (#64)
- **Actions bump counter (inflated count):** `actions_summary()`'s `bumps` counter now dedupes by `(action, pin)`, matching the deduped rows the report renders. Previously the same action+pin appearing in multiple workflow files inflated the summary count above the number of displayed rows. (#65)
- **Hatch uv-guard (uv.lock paths):** The `uv`-on-PATH guard added in #33 now also protects the `uv.lock` check/apply paths (`_check_uv_lock` / `_apply_uv_lock`), so a hatch project with a `uv.lock` but no `uv` on PATH degrades gracefully (warn + no-op) instead of crashing. (#66)
- **bun checker (real `bun` dependency):** A dependency literally named `bun` is no longer rejected by name and is now detected as outdated and upgraded. The `bun outdated` banner that motivated the original exclusion is filtered by structure (it is not a `│`-delimited table row), so the false-negative is fixed without reintroducing the banner false-positive. (#67)
- **Verbose remediation diagnostics (JS):** `repo-ops-upgrade-remediations-js` imported `VERBOSE` by value at import time, so the per-remediation `detect=` diagnostic stayed dead under `--verbose` once `set_verbose()` rebound the module global. It now reads `process.VERBOSE` live. (#68)
- **Inventory on-disk count (nested duplicates):** `count_installed_node` no longer prunes `node_modules` during its walk, so nested `node_modules/**/node_modules` duplicates from version conflicts are counted as the on-disk column's docstring promises instead of being silently excluded. The shared `SKIP_DIRS` constant is untouched (a local skip set drops only `node_modules` for this walk). (#69)
- **Checker timeouts (hatch, pnpm, poetry):** `_check_uv_lock` (hatch), `check_pnpm`, and `check_poetry` now pass `TIMEOUT_CHECK` (600 s) to their dry-run subprocesses instead of the 300 s default, matching their apply paths — the sibling fix to #57/#64 for the three remaining managers. (#70)
- **Discovery (`git_common_dir` timeout):** A `git rev-parse --git-common-dir` that exceeds its 10 s timeout in `discover_repos` is now caught (warn + skip that repo) instead of an uncaught `TimeoutExpired` aborting the entire scan — the sibling fix to #41. (#71)
- **Post-apply commit (quoted paths & renames):** `_parse_porcelain` now unquotes git's C-style-quoted porcelain paths (spaces / non-ASCII, decoded as UTF-8) and extracts the new path from `R old -> new` rename entries, so `git add` no longer fails on a literal-quoted pathspec under a directory containing a space. (#72)
- **pip-compile fallback (`--upgrade`):** The pip-compile dry-run check (used when `uv` is absent) now passes `--upgrade`, so `plan` reports the same upgrades `apply` performs instead of preserving current pins and under-reporting zero changes — the pip-compile sibling of #54. (#73)
- **Actions behind-latest counter (inflated count):** `actions_summary()`'s `behind_latest` counter now dedupes by `(action, pin)` on the same `seen` set as the `bumps` counter, so the "newer major available" footer no longer over-reports when one action+pin is pinned across multiple workflow files — the sibling of #65. (#74)
- **Inventory dep-edge count (`node_modules`):** `count_dep_edges` now prunes `node_modules` from its walk, so the `edges` column counts only declared dependencies (parity with the Python edge counters) instead of ballooning to the sum of every installed package's declared deps. (#75)
- **npm/pnpm behind-latest bucketing:** `check_npm`/`check_pnpm` now bucket out-of-range upgrades (newest `latest` outside the declared semver range) into `behind_latest` and return the `(changes, behind_latest)` tuple, so `plan` and the bump counter reflect what `npm/pnpm update` actually applies instead of listing unreachable `latest` bumps as actionable — the npm/pnpm completion of #22. (#76)

## [0.7.0] - 2026-06-04

### Added

- **Report — behind-latest disposition:** Workspaces with packages that have a newer version available but are not actioned (because latest-mode is off) are now tracked on a distinct `behind_latest` field of `WorkspaceResult` rather than being silently dropped into `status: ok`. Affected managers: **bun** (detects packages where `latest` column > in-range `update` column), and **GitHub Actions** (actions where a newer major is available but cross-major upgrades are intentionally not auto-applied). A summary counter (`packages_behind_latest`, `actions_behind_latest`) and a human footer hint are emitted when any behind-latest items are found. Exit code and `failure_count` are unchanged — behind-latest is report-only. The `WorkspaceResult.to_json()` output gains an additive `behind_latest` array; existing fields are unchanged. Yarn's behind-latest detection is deferred (requires a second install pass) — see #22 for details. (#22)

### Changed

- **GitHub Actions SHA-pin policy (behavior change vs #21):** `scan_actions` now defaults to keeping SHA pins (`keep_sha=True`). Converting a hardened `actions/checkout@<sha>` to a floating major tag now requires explicit opt-in via `--actions-unpin-sha`; the inline version-hint comment (`# v4`) is preserved on SHA-pinned lines. The previous flag `--actions-keep-sha` is retained as a deprecated no-op. This reverses the default established by #21. (#25)

### Fixed

- **Discovery:** Bare default-root `plan`/`apply` now discovers package-manifest workspaces (npm/yarn/pnpm/bun, Go, Python, Rust) at any depth below a root, not just at a fixed `<root>/<org>/<proj>` layout. The non-flat walker previously hardcoded a two-level descent, so checkouts laid out as `/usr/local/src/com.github/<org>/<repo>` (or flat roots like `~/Projects`) silently yielded **zero** workspaces while Actions drift still surfaced — a false sense of completeness. Package discovery now reuses the same depth-agnostic git-repo walk that drives Actions discovery, so both ecosystems cover the same repos. Also surfaces repos nested one level deeper (e.g. `<org>/Bastion/<repo>`) whose dependency drift was previously invisible at every root depth. (#23)
- **Discovery (multiple workspaces per repo):** Each repo is now scanned recursively, so *every* manifest in its tree is enumerated rather than just one per repo. A repo with a Go module at its root and a node app in `web/` now yields both workspaces; repos whose only manifest sits in a subdirectory (`web/`, `www/`, …) are discovered again. uv workspace members (`[tool.uv.workspace].members`, governed by the root `uv.lock`) and npm/yarn `workspaces` members are suppressed so monorepos surface once at the root rather than exploding into per-member entries. (#24)
- **Discovery (no silent coverage gaps):** A manifest that is found but not actionable — `package.json` with no lockfile/`packageManager`, `pyproject.toml` with no recognized manager, `Cargo.toml` with no lock and no `[package]`/`[workspace]`, or any manifest outside a git repo — is now recorded as a **counted** discovery skip with a reason code instead of being dropped silently. The count is collected for human runs too (not only `--json`) and the footer lists each skip with its path and reason, so a coverage gap is no longer indistinguishable from "nothing to upgrade". (#24)
- **Discovery (`requirements.txt` manifest):** Non-flat discovery now includes `requirements.txt` as a trigger file, so a repo whose only manifest was a requirements.txt was no longer silently skipped by the package walker. (#32)
- **Discovery (find-subprocess timeout):** The `discover_repos` find subprocess timeout is now tunable via the `REPO_OPS_FIND_TIMEOUT` environment variable or a `timeout=` parameter; the default remains 60 s. (#34)
- **Discovery (submodule hang):** `apply --submodule-order=children-first` no longer hangs on a registered-but-uninitialized submodule. The topo-sort now skips self-referential links and enforces an iteration cap so a degenerate graph can't spin forever. (#36)
- **Discovery (vendored `.gitmodules`):** `_find_gitmodules` now prunes `SKIP_DIRS` during its walk, so it no longer descends into `node_modules`, `target`, `.venv`, or similar directories and registers vendored `.gitmodules` files as real submodule roots. (#43)
- **Discovery (duplicate SKIP output):** Skip-only directories no longer double-print a `SKIP` line to stderr in nested-repo overlap scenarios; the `seen_paths` set is now updated unconditionally on first encounter. (#46)
- **Discovery (self-referential submodule guard):** The self-referential submodule guard now also catches a `.gitmodules` entry whose `path` field is `.` (the repo root itself). (#47)
- **Discovery (hoisted workspace member counts):** Hoisted yarn/npm workspace members are no longer mis-counted as discovery skips. Workspace-membership is now checked before manager detection, keeping the `discovery skipped` counter meaningful. (#49)
- **Discovery (Cargo workspace members):** Cargo workspace members that have a `[package]` section but no own lockfile are now suppressed instead of each surfacing as a standalone Rust workspace. Known limitation: implicit auto-discovery, `exclude`, and `default-members` are not yet handled. (#52)
- **pnpm lockfile parser:** pnpm lockfiles at `lockfileVersion ≤ 6` using path-notation are now parsed correctly. Additionally, v5 path-notation lines containing an `@` in the peer-dependency suffix are no longer garbled by the `name@version` regex. (#28, #53)
- **Rust checker (stderr):** `check_rust` now reads cargo's stderr (where `Updating … -> …` lines are written), so Rust workspaces correctly report available updates instead of always appearing clean. (#30)
- **Poetry apply (PATH guard):** `apply_poetry` now enforces the same `poetry`-on-PATH guard that `check_poetry` already had, preventing a silent failure when poetry is absent at apply time. (#33)
- **pnpm checker (JSON errors):** `check_pnpm` now warns with a raw-output snippet on `JSONDecodeError` instead of silently returning an empty update list. (#35)
- **Hatch checker:** `check_hatch` now verifies return codes for `uv venv`, `uv pip install`, and `uv pip list --outdated`, surfacing failures instead of silently returning empty results. It also detects the extras group from `[project.optional-dependencies]` (trying `dev` → `test` → `all` → `lint`) instead of hardcoding `.[dev]`. (#37, #44)
- **pip-tools checker:** `check` now compiles with `--upgrade` so the plan reflects the same resolver behavior as `apply`. Previously, a plan could show "nothing to do" and then apply would change everything. (#54)
- **Yarn in-range bump detection:** In-range `yarn.lock` bumps (e.g. `4.17.20` → `4.17.21` within `^4.17.0`) are now detected by diffing the resolved `version:` lines in the lockfile rather than comparing range descriptors, which never changed. (#56)
- **Checker timeouts:** `check_npm` and the `go list` check calls now use `TIMEOUT_CHECK` (600 s) instead of the default 300 s, matching the timeout used by `apply`. (#57)
- **Overrides audit (protocol pins):** `overrides_audit` no longer emits false-positive "stale" or "blocks upgrade" warnings for protocol-style override pins (`workspace:`, `file:`, `link:`, `portal:`, `git+`, `github:`, `https:`, `npm:`, `catalog:`, `http:`, `patch:`). (#27, #50)
- **Version normalization:** `normalize_version` now strips a leading `v` or `V` so `semver_tuple("v4.1.0")` and `semver_tuple("V4.1.0")` compare correctly against bare version strings. (#29, #58)
- **Bun checker (overrides audit on clean runs):** The bun tuple-checker now always runs `overrides_audit`, even when the new `behind_latest` list is empty. Previously override auditing was skipped on the common clean-run path. (#42)
- **Bun parser (double-count):** The bun text parser no longer double-counts a package into both `changes` and `behind_latest`. (#45)
- **Actions tag cache:** GitHub Actions tag resolution is now cached at the module level across all repos in a multi-repo run. The per-call cache was previously bypassed, causing redundant API fetches and unnecessary rate-limit consumption. (#31)
- **Actions human report:** The human-readable Actions report no longer prints no-op `vX → vX [ok]` rows or duplicate lines (deduped by `(action, pin)` pair). JSON output is unchanged. (#39)
- **Run-summary footer:** The run-summary footer table now includes an `actions failures` row. (#48)
- **`--on-verify-fail=revert`:** The revert path now checks the `git checkout` return code, removes any untracked files that `apply` created (via a `git clean` scoped to the workspace), and records a `reverted_ok` flag so a failed revert can never be reported as `FAILED_CLEAN`. (#26)
- **Push-audit timeout:** A git `TimeoutExpired` in the push-audit reflog and subject lookups is now caught; previously it aborted the entire scan. (#41)
- **Parallel pool error handling:** `run_bounded_pool` now guards `fut.result()` so an exception raised by a worker thread no longer abandons the pool's remaining futures. (#51)
- **`tee_stdout_stderr` cleanup:** `tee_stdout_stderr` now always restores `sys.stdout` and `sys.stderr` in its `finally` block, even if the flush itself raises. Previously a flush failure left the streams pointed at a closed file for the rest of the process. (#55)

### Security

- **`apply` SHA-pin preservation (see Changed):** Hardened `actions/checkout@<sha>` pins are no longer silently converted to floating major tags by default. Unpin behavior requires `--actions-unpin-sha`. (#25)
- **`install-skill --force` source-tree guard:** `install-skill --dest <dir> --force` now refuses when the destination resolves to, contains, or is contained by the source checkout. Previously `--force` could `rmtree` the source tree before the copy began. (#40)

## [0.6.1] - 2026-06-03

### Fixed

- **CLI:** Bare subcommands (e.g. `repo-ops plan` with no path args) no longer fail with `root does not exist: <cwd>/plan`. The dispatcher forwarded `None` instead of the empty remainder list, so the handler's argparse fell back to `sys.argv[1:]` and re-consumed the subcommand token as a positional root. Bare subcommands now correctly scan `DEFAULT_ROOTS`.
- **Types:** Resolved all mypy errors across `inventory`, `upgrade`, and `core` (including a real `_has_actionable_repo_changes` return-type bug that returned a list where `bool` was declared). The CI mypy gate is now green.

## [0.6.0] - 2026-06-03

### Changed

- **GitHub Actions pin policy:** Tag-pinned actions (e.g. `v3.19.0`, `v4`) now normalize to the floating bare-major tag for the *same* major (`v3.19.0` → `v3`, `v4` stays `v4`). Cross-major upgrades (e.g. `v4` → `v6`) are reported but never auto-applied. SHA pins remain report-only. This eliminates per-patch churn on apply consumers and prevents silent breaking major bumps. **Behavior change:** `apply` now writes `v3` instead of `v3.19.0`; consumers that expected exact-patch replacement will see floating major tags instead.

## [0.5.0] - 2026-05-31

### Added

- **Discovery:** `--flat` mode and actions-only repositories (workflow pins without package manifests) participate in plan/apply scans.
- **pip-tools apply:** `requirements.txt` updates via `uv pip compile` when a native pip-tools apply path is unavailable.
- **Release CI:** Tag builds run `pytest` before uploading wheel/sdist artifacts.

### Changed

- **pnpm / yarn:** Default to conservative semver updates (aligned with npm); Yarn isolated plan/apply uses `yarn up` unless `REPO_OPS_YARN_UPDATE_LATEST=1`.
- **Post-apply:** Lockfile snapshot diff detects more change kinds; post-apply commits are scoped on dirty worktrees; checker failures are logged instead of swallowed silently.
- **GitHub Actions:** Actionlint noise reduced; `gh` tag lookups batched; apply skipped when the worktree is dirty.
- **Docs:** Ecosystem upgrade modes, discovery flags, and tier docs no longer pin release versions in agent/human tiers.

### Fixed

- **Security / plan isolation:** Allowlist `packageManager` values; skip Corepack during plan check; Hatch plan checks use a temporary venv; `install-skill` rejects unsafe `--dest` paths; discovery validates cwd and scan roots; default `find` omits `-L` follow symlinks.
- **Parallel apply:** Fail-fast batch exit no longer raises `KeyError` when a sibling repo stops early.
- **CLI:** Reject non-positive `--jobs`.
- **Bun:** Pass `--` before package update targets so flags are not swallowed.
- **Go:** Harden explicit `go get` batches.
- **Inventory:** Skip scan when a default root is missing; prune `SKIP_DIRS` inside `node_modules` dependency counts.
- **pushed:** Resolve pre-push OID from reflog via `rev-parse`.
- **Upgrade apply:** Narrow swallowed exceptions; guard submodule walks; submodule and Actions apply behavior on partially dirty trees.

### Docs

- HUMANS: Go/Bun/pnpm/Yarn upgrade modes, `--flat` / actions-only discovery, and conservative Yarn policy.

## [0.4.0] - 2026-05-31

### Added

- **CLI reliability:** `--log-file` for plan/apply transcripts; `--strict-exit` and verify-failure paths return reliable non-zero exit codes for CI and shell `pipefail`.
- **Plan artifacts:** `--json-out` writes the full plan JSON atomically to a file (stdout contract unchanged).
- **Run summary:** End-of-run roll-up footer with per-repo counts and a capped failure list; JSON reports include matching summary fields ([#14](https://github.com/Rethunk-Tech/repo-ops/issues/14)).
- **Apply policy:** `--on-verify-fail` with `revert` (default keep) to roll back apply changes when verification fails.
- **Submodules:** Discovery, `children-first` ordering via `--submodule-order`, and pointer hints in plan JSON ([#13](https://github.com/Rethunk-Tech/repo-ops/issues/13)).
- **Post-apply commits:** `--commit=conventional|agent` with `--commit-dry-run` and `--commit-on-fail`; pluggable commit-agent providers (default Cursor SDK) behind optional `repo-ops-upgrade[commit-agent-cursor]` ([#15](https://github.com/Rethunk-Tech/repo-ops/issues/15)).
- **GitHub Actions:** Workflow `uses:` pins discovered and updated via ruamel.yaml AST (no substring pin corruption); optional `gh api` resolution for latest action tags; plan/apply output reports workflows scanned.

### Changed

- **Breaking (install only):** Optional Cursor commit-agent extra is named `commit-agent-cursor` (`agent` remains an alias in `pyproject.toml`). Install: `uv pip install 'repo-ops-upgrade[commit-agent-cursor]'`.
- Plan/apply JSON may include new summary, submodule, and Actions-scan fields; consumers should tolerate unknown keys.

### Fixed

- **Bun plan:** Prefer JSON `outdated` output and filter table junk from stdout parsing ([#12](https://github.com/Rethunk-Tech/repo-ops/issues/12)).
- **JS remediations apply:** Report apply outcome text instead of repeating plan copy ([#11](https://github.com/Rethunk-Tech/repo-ops/issues/11)).

### Docs

- HUMANS and bundled skill: bulk-apply playbook, post-apply commit flags, MCP vs headless commit paths, Bastion submodule workflow, `--on-verify-fail`, summary footer, and `--json-out` usage.

## [0.3.5] - 2026-05-31

### Fixed

- **Go mvs apply:** use `go get -u -t all` instead of `go get -u ./...` / `go get -t -u ./...` so transitives and indirects update in one step.

### Changed

- **Docs:** tier dedup (HUMANS install/usage/imports; AGENTS layout/process); install via `packages/repo-ops` and git `#subdirectory=packages/repo-ops`.

## [0.3.4] - 2026-05-31

### Fixed

- **Bun apply:** run `bun update <pkg>@<version>` from `check_bun` results instead of `bun update --latest`, which rewrote lockfile roots to the literal `"latest"` specifier (#8).

## [0.3.3] - 2026-05-31

### Fixed

- **Go plan/apply:** parse `go list -m -u -json` stdout with a streaming JSON decoder so pretty-printed multi-line module objects work (previously treated output as JSONL and failed every Go root).

## [0.3.2] - 2026-05-30

### Fixed

- **GitHub Actions apply:** replace workflow pins only on matched `uses:` lines so shorter tags are not applied as substrings of longer pins (#5).
- **Yarn plan:** isolated snapshots copy `.yarn/releases/` (Berry) and `file:../` sibling checkouts; failed `yarn up` / `install` errors include a stderr tail in plan JSON (#6).
- **Go upgrades:** default `patch` mode aligns plan and apply (`go list -m -u -json` with patch filter; `go get -u=patch all` + tidy). Opt into `explicit` or legacy `mvs` via `REPO_OPS_GO_UPGRADE_MODE` (#7).

### Changed

- HUMANS.md documents Go upgrade modes and Yarn isolated-plan copy semantics.

## [0.3.1] - 2026-05-31

### Added

- Bundled agent skill (`repo_ops/skills/repo-ops/SKILL.md`) and `repo-ops install-skill` to copy it into `~/.cursor/skills/repo-ops/` and `~/.claude/skills/repo-ops/` (`--target`, `--force`, `--dry-run`, `--home`, `--dest`).

### Fixed

- `repo-ops plan` / `apply` with `--json`: discovery SKIP/WARN lines no longer print during the scan; they appear in JSON `skipped` / `warnings` arrays so stdout stays a single JSON document.

## [0.3.0] - 2026-05-30

### Added

- **uv workspace** under `packages/*` (20 internal packages): `repo_ops_core`, `repo_ops_models`, `repo_ops_lockfiles`, `repo_ops_js` (+ npm/pnpm/bun/yarn plugins), `repo_ops_go`, `repo_ops_rust`, `repo_ops_py` (+ uv/poetry/pip-tools/hatch plugins), `repo_ops_upgrade`, `repo_ops_upgrade_remediations_js`, `repo_ops_inventory`, `repo_ops_pushed`, meta `repo_ops`.
- Explicit ecosystem registry (`repo_ops_upgrade.registry`) populated by `repo_ops._register.register_all()` at CLI startup.
- Lockfile parser unit tests in `packages/repo-ops-lockfiles/tests/`.

### Changed

- **Breaking:** Monolith `src/repo_ops` removed. Programmatic imports use `repo_ops_*` package modules (see HUMANS package-layout table).
- **Breaking:** Single console script **`repo-ops`** with subcommands `inventory`, `plan`, `apply`, `pushed` (no flat `repos-*` entry points).
- Lockfile parsing and snapshot/diff consolidated in `repo_ops_lockfiles`; post-apply change detection uses injected checker (no eager `CHECKERS` import hub).
- Shared manifest walks: `repo_ops_core.walk` (`iter_scanned_dirs`, `manifest_kinds`).
- Upgrade parallel execution: `repo_ops_core.parallel.run_bounded_pool`.

### Removed

- **Breaking:** `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, `repos-pushed` console scripts.
- **Breaking:** `repo_ops.upgrade.ecosystems` package and compatibility re-exports.
- `reject_legacy_upgrade_flags` and legacy dep_refresh flag migration hints.

## [0.2.5] - 2026-05-29

### Fixed
- CLI tables: shared column padding so headers align with data (`repos-inventory`, `repos-pushed`).
- Inventory `--flat .`: use resolved directory name when `Path('.').name` is empty (Python 3.12+).

## [0.2.4] - 2026-05-29

### Fixed
- Discovery: find `.git` directories without pruning them away (fixes `repos-pushed` and shared repo discovery).
- Inventory: count Python dependency edges from `uv.lock` and `pyproject.toml` lockfiles.

### Changed
- Documentation tier dedup: README defers install details; AGENTS and CONTRIBUTING each hold a single source per tier.

### Added
- HUMANS: document editable `uv tool` install and verification steps for contributors.
- Tests covering repo discovery and `repos-pushed` regression for `.git` pruning.

## [0.2.3] - 2026-05-29

### Changed
- Migrated packaging from hatchling to `uv_build`; project is uv-locked (`uv.lock`, `.python-version`).
- Dev dependencies moved to `[dependency-groups] dev` (replacing `[project.optional-dependencies] dev`).
- CI and contributor docs use `uv sync`, `uv run`, and `uv build` instead of pip/venv.

### Added
- Canonical install documented via `uv tool install` (git URL today; PyPI when published).
- HUMANS installation section for runtime (`uv tool`) vs development (`uv sync`).

## [0.2.2] - 2026-05-29

### Changed
- JSON upgrade output moved to `upgrade/report.py` (`build_json_payload`, `print_json_report`).
- Lockfile snapshot/diff helpers extracted to `upgrade/ecosystems/node_lockfiles.py`.

### Added
- HUMANS package-layout table for programmatic imports (0.2.1+).
- Subsystem tests (actions, remediations, orchestration, report, inventory `--help`).
- CLI `--help` smoke tests invoke domain `main()` directly (no PATH dependency).

## [0.2.1] - 2026-05-29

### Changed
- Decomposed `upgrade/engine.py` into focused modules: `constants`, `workspaces`, `actions`, `remediations`, `verify`, `report`, `orchestration`, and `ecosystems/` (`node`, `yarn`, `go`, `rust`, `python`).
- Test modules renamed for discoverability: `test_upgrade`, `test_inventory`, `test_pushed`.

### Removed
- Monolithic `upgrade/engine.py` (logic moved to submodules above; import `repo_ops.upgrade.ecosystems` or specific submodules instead).

## [0.2.0] - 2026-05-29

### Added
- Four intent-based console entry points: `repos-inventory`, `repos-upgrade-plan`, `repos-upgrade-apply`, `repos-pushed`.
- Optional `repo-ops` dispatcher with subcommands `inventory`, `plan`, `apply`, `pushed`.
- Domain package layout: `core/`, `inventory/`, `upgrade/`, `pushed/`.
- PEP 561 `py.typed` marker.

### Changed
- **Breaking:** Removed `repo-dep-count`, `repo-dep-refresh`, and `repo-pushed-commits` script names.
- Split dependency refresh into explicit plan (read-only) and apply commands.
- GitHub Actions pin audit is on by default for upgrade plan/apply; use `--skip-actions` to omit.
- `repos-pushed` uses unified root arguments (`add_roots_argument`).

### Removed
- `-y` / `--yes`, `--also-actions`, and `--only-actions` flags (migration messages point to new commands).

## [0.1.0] - 2026-05-29

### Added
- Initial bootstrap of the `repo-ops` package as a lightweight Python src-layout project using hatchling for packaging.
- Three console entry point CLIs registered in pyproject.toml: `repo-dep-count`, `repo-dep-refresh`, and `repo-pushed-commits`.
- Supporting community and release documentation (README, HUMANS, AGENTS, CONTRIBUTING, SECURITY, LICENSE, and this CHANGELOG).
- GitHub community health files (PR/issue templates, CODEOWNERS, Dependabot, minimal CI for lint/test).
- Agent coordination policies (AGENTS.md) and mandatory MCP-based incremental conventional commits for automation.
- Python >= 3.10 compatibility, Ruff linting/formatting, pytest configuration, and .editorconfig hygiene.

### Changed
- N/A (initial release).

[Unreleased]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.5...v0.4.0
[0.3.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.5...v0.3.0
[0.2.5]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Rethunk-Tech/repo-ops/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rethunk-Tech/repo-ops/compare/0000000...v0.1.0
