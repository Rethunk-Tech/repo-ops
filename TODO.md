# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue body still says “latest: v0.18.0”; bump the note when touching the issue.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds for pin ecosystems (container / Actions / pre-commit / submodules)

`[[hold]]` rules are documented as not yet consulted by container, GitHub Actions, submodule, and pre-commit checkers (same schema, no enforcement). Rust and Deno are also outside the current enforcement matrix.

**Refs:** `docs/usage.md` § Package holds (“Not yet wired…”); `repo_ops_core.holds`; checkers in `packages/repo-ops-container`, `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py`, `precommit.py`, `submodules.py`; `packages/repo-ops-rust`, `packages/repo-ops-js/src/repo_ops_js/deno.py`.

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, pre-commit `rev`, or submodule ref caps plan/apply the same way JS/Go/Python do; docs list each ecosystem; expired holds still skip without rewriting config.

### 1.3 Cargo workspace discovery: `exclude` / implicit members / `default-members`

Child Rust workspaces are suppressed only via explicit root `[workspace].members` globs. Cargo also auto-includes path deps, supports `exclude`, and uses `default-members` for default package selection — none of those are handled, so some layouts still double-count members as standalone rust workspaces.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/discovery.py` (`cargo_workspace_members`, `path_is_cargo_workspace_child`); Cargo Book [workspaces — members / exclude / default-members](https://doc.rust-lang.org/cargo/reference/workspaces.html); CHANGELOG known limitation under #52.

**Traps:** Empty/`members` omitted ≠ “no children”; path dependencies under the workspace root are members even without a glob. `default-members` affects *which* packages cargo operates on by default, not membership — collapsing discovery must not confuse the two. Prefer matching `cargo metadata --no-deps` over re-implementing glob rules if feasible.

**Acceptance:** Virtual and non-virtual workspaces with `exclude`, globs, and implicit path-dep members surface a single rust workspace at the root; no duplicate member rows in `plan --json` for those layouts.

### 1.4 Close stale issue #155 (pyrefly verify gate)

Verify already recognizes `pyrefly` in target-repo dev deps (`uv run pyrefly check .`). Issue [#155](https://github.com/Rethunk-Tech/repo-ops/issues/155) is still **OPEN** with no comments despite the 0.20.4 fix.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (mypy then pyrefly branches); `CHANGELOG.md` 0.20.4.

**Traps:** Closing is hygiene only — do not re-implement. Confirm the shipped branch matches the issue’s suggested detection surface (uv / hatch / poetry dev deps) before closing.

**Acceptance:** [#155](https://github.com/Rethunk-Tech/repo-ops/issues/155) closed with a pointer to the releasing commit/version; no code change required if coverage already matches.

---

## Round 2 — Ecosystem parity

### 2.1 Deno `--deno-targeted-bump` (JS targeted-bump parity)

Deno reports `behind_latest` and honors `--latest` / `REPO_OPS_DENO_UPDATE_LATEST`, but unlike yarn/npm/pnpm/bun there is no reversible per-package targeted-bump fallback to *move* remaining gaps.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/deno.py` (`check_deno` / `apply_deno`); JS siblings `targeted_bump_behind` in `repo-ops-js-{yarn,npm,pnpm,bun}`; CLI flags in `packages/repo-ops-core/src/repo_ops_core/cli.py`; `AGENTS.md` full-coverage sweep table.

**Traps:** Deno’s updater is `deno outdated --update` (± `--latest`); a targeted path must snapshot/restore `deno.lock` + import maps like the JS managers. Ghost-bump guard is best-effort when `node_modules` is absent — targeted bump must not assume npm layout. Do not fold this into `--latest` implicitly (same trap as other `--*-targeted-bump` flags).

**Acceptance:** `--deno-targeted-bump` (and config/env twin) moves still-behind direct deps after the bulk update, reverts on no-advance/install failure, and appears in the full-coverage sweep docs beside the other four flags.

### 2.2 Dependabot high-confidence supersession for poetry / PDM / pipenv locks

Scaffold treats `poetry.lock` / `pdm.lock` / `Pipfile.lock` as ecosystem indicators, but high-confidence supersession only parses npm/yarn/pnpm/bun/`uv.lock` (plus go.mod / Cargo.lock). Poetry/PDM/pipenv PRs fall through to low-confidence text search and are never auto-closed.

**Refs:** `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (`_resolved_lockfile_versions` / `_lockfile_versions`); parsers in `packages/repo-ops-lockfiles` (`versions_pdm_lock`, `versions_pipenv_lock`; poetry via shared TOML `[[package]]` shape used in SBOM); license scanners already read these locks.

**Traps:** PEP 503 normalize Python names before lookup (uv path already does). Grouped Dependabot PRs must stay “all rows high-confidence” before `--close`. Reuse lockfile parsers — do not scrape lock text.

**Acceptance:** A Dependabot PR whose versions are already in `poetry.lock` / `pdm.lock` / `Pipfile.lock` is high-confidence superseded and closable with `--close`; JSON report marks `low_confidence=false`.

### 2.3 SBOM / license: enrich SPDX IDs instead of permanent `NOASSERTION`

`repo-ops sbom` always emits license `NOASSERTION` because lock parsers carry no license ids; `repo-ops license` is offline/best-effort and still leaves poetry/PDM/pipenv unknown without a venv, and containers always unknown.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/sbom.py`; `license_policy.py`; `docs/usage.md` § license scope table.

**Traps:** Network enrichment (crates.io / npm / PyPI) must be opt-in and cacheable — default SBOM path should stay offline. Do not invent licenses; prefer registry metadata or on-disk `LICENSE` / `METADATA`. Container images have no local license source today — keep them out of exit-`2` unknown unless a deliberate source is added.

**Acceptance:** Optional enrichment fills real SPDX ids for common npm/PyPI/crates packages in SBOM output; `license` unknown rate drops for poetry/PDM/pipenv when enrichment or a declared cache is enabled; offline default behaviour unchanged.

### 2.4 Rust upgrade-mode honesty vs aggressive bumps

`--latest` does not change Cargo behaviour (`cargo update` stays within existing constraints). Operators cannot opt into conservative vs aggressive Rust bumps the way Go (`patch`/`explicit`/`mvs`) or container cross-major gates work. Documented as intentional — still a fleet gap when Rust majors need a deliberate path (e.g. `cargo upgrade` / manifest edits).

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `docs/usage.md` ecosystem upgrade-modes table; audit remediations already use `cargo update -p … --precise`.

**Traps:** Inventing a fake `--latest` that only re-runs `cargo update` would lie. Manifest-editing upgrades are a different product surface from lockfile refresh — keep the boundary explicit. Holds (1.2) should land before or with any aggressive Rust mode.

**Acceptance:** Either a real opt-in Rust upgrade mode (documented, gated, tested on manifest+lock) **or** a single canonical doc/AGENTS note that Rust majors are out-of-scope for repo-ops and must be done by hand — no half-mode.

---

## Round 3 — Operator DX / release hygiene

### 3.1 Single full-coverage sweep flag

Routine bulk maintenance needs `--latest` **plus** `--actions-refresh-sha --container-refresh-sha --{yarn,npm,pnpm,bun}-targeted-bump`. Agents and humans routinely pass bare `--latest` and under-report drift. AGENTS already documents the combo; the CLI still requires remembering every flag.

**Refs:** `AGENTS.md` § Full-coverage sweep; `packages/repo-ops-core/src/repo_ops_core/cli.py` upgrade-policy group; `docs/usage.md`.

**Traps:** A new umbrella (e.g. `--full` / `--catch-all`) must not silently change default `plan`/`apply` behaviour — opt-in only. Composing with per-ecosystem overrides (`--go-upgrade-mode`, `--actions-upgrade-mode`) must keep “explicit flag wins” semantics. If Deno targeted-bump (2.1) lands, include it in the umbrella.

**Acceptance:** One documented flag enables the full catch-all set; `plan --help` / AGENTS point at it; bare `--latest` help text warns it is not full coverage.

### 3.2 Automate GitHub Release post-cut cleanup

Every `v*` release still needs manual `gh release edit` (CHANGELOG section as notes) and `gh release delete-asset … default.gitignore`. Easy to forget; leaves a generic Release body and a junk asset.

**Refs:** `AGENTS.md` § Cutting a release step 6; `.github/workflows/release.yml` `build` job.

**Traps:** Prefer fixing the workflow artifact list (stop uploading `default.gitignore`) over forever deleting it. Notes extraction must match Keep a Changelog `## [X.Y.Z]` sections. Do not auto-publish to PyPI here (see 1.1).

**Acceptance:** A tagged release has correct CHANGELOG-derived notes and no `default.gitignore` asset without a human cleanup pass.

### 3.3 Doctor probes for verify-critical tools (`pyrefly`, `hatch`, `pre-commit`)

`repo-ops doctor` probes the historical binary set but not `pyrefly` (now a first-class verify gate), `hatch`, or the `pre-commit` CLI used when refreshing hook revs.

**Refs:** `packages/repo-ops-doctor/src/repo_ops_doctor/doctor.py` (`_BINARIES`); `verify.py` pyrefly branch; `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py`; `docs/usage.md` doctor list.

**Traps:** Keep doctor read-only and root-free. `cargo-audit` is a subcommand probe pattern to mirror if needed. Version probes must not hang (empty `version_args` / `DEVNULL` stdin pattern).

**Acceptance:** `repo-ops doctor --json` reports found/missing for `pyrefly`, `hatch`, and `pre-commit`; HUMANS/usage lists match the probe table.

### 3.4 Stale `--commit-umbrella` “deferred to cli.py” comment

`--commit-umbrella` is already wired in `repo_ops_core.cli` and honored in `run.py` (`getattr(args, "commit_umbrella")`), but a comment still says the CLI flag is deferred.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (~line 652); `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--commit-umbrella`); shipped in #125 / `7f92ec6`.

**Traps:** Docs-only / comment cleanup — do not change behaviour. Confirm `REPO_OPS_COMMIT_UMBRELLA` and the flag stay equivalent.

**Acceptance:** Comment and any leftover “deferred” wording match the shipped CLI; no behaviour change.

### 3.5 Hatch verify docs mention mypy but not pyrefly

Usage still describes hatch verify fast gates as including mypy when configured, without naming pyrefly, while verify code checks both.

**Refs:** `docs/usage.md` hatch / `--migrate-to-uv-lock` paragraph; `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py`.

**Traps:** Doc drift only. Keep the “no `uv.lock` → skip `uv sync --locked`” behaviour clear.

**Acceptance:** Docs list mypy **and** pyrefly as recognized type-check gates for hatch (and generally) in the same breath as the code.

---

## Round 4 — Hygiene carry-forward

### 4.1 Supersede Dependabot PR #158 by hand

Open Dependabot PR [#158](https://github.com/Rethunk-Tech/repo-ops/pull/158) bumps the `dev-dependencies` group (`ruff`, `pyrefly`). Fleet rule: never merge Dependabot — apply the equivalent bump on `main` and close as superseded.

**Refs:** fleet `dependency-policy` skill; `uv.lock` / root `pyproject.toml`; `repo-ops apply --flat` on this checkout (or equivalent manual bump).

**Traps:** Local `main` may already be ahead of `origin/main`; reconcile lock drift before closing. Do not approve/merge the Dependabot PR.

**Acceptance:** `main` carries the intended ruff/pyrefly versions; #158 closed superseded; no Dependabot merge commit on the default branch.

### 4.2 Refresh issue #124 status text

Issue title/body still frame “latest: v0.18.0” while the tree has moved through 0.22.x. Operator hold remains valid; the status note is stale.

**Refs:** [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124); GitHub Releases / tags.

**Traps:** Do not treat a status edit as enabling PyPI. Keep the “intentional hold / not a CI bug” framing.

**Acceptance:** Issue title/body cite the current release line and restate the OIDC publisher gap without implying active chase.

### 4.3 Dependabot supersession for legacy `bun.lockb`

Inventory best-effort-parses binary `bun.lockb` via `bun` dump; dependabot high-confidence resolution only walks text `bun.lock`. Repos still on `lockb` keep low-confidence Dependabot closes.

**Refs:** `packages/repo-ops-inventory/src/repo_ops_inventory/counting.py` (`_versions_bun_lockb`); `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (`bun.lock` only).

**Traps:** Invoking `bun` on lockb needs the binary on PATH and a timeout; cache or skip cleanly when bun is missing. Prefer encouraging text `bun.lock` migration over forever supporting lockb if fleet has moved on.

**Acceptance:** Either high-confidence supersession works for `bun.lockb` when `bun` is available, or docs explicitly say lockb is unsupported for `--close` and inventory remains the only consumer.

---

## Round 5 — Second-pass gaps

### 5.1 Configurable commit-agent model

`--commit=agent` hardcodes Cursor SDK `model="composer-2.5"` in the cursor provider. Fleet model routing and operator preference change; a fixed slug drifts.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/commit_agent_cursor.py`; `commit_agent_providers.py`; optional extra `repo-ops-upgrade[commit-agent-cursor]`.

**Traps:** Default can stay `composer-2.5` for stability; override via env/config (e.g. `REPO_OPS_COMMIT_AGENT_MODEL`) — arrays/objects stay out of env, a single string is fine. Invalid model must fail closed with a clear error, not silently fall back to an unexpected model.

**Acceptance:** Documented env/config selects the SDK model; unset keeps today’s default; help/`--commit=agent` docs mention the knob.

### 5.2 Verify pyrefly invocation ignores project excludes

`verify` runs bare `uv run pyrefly check .`. Pyrefly’s path mode ignores `[tool.pyrefly] project-includes/excludes` unless `--project-excludes` is passed on the CLI — the same trap this repo’s own CI documents. Target repos that exclude `tests/**` / `scripts/**` (or similar) can fail verify on paths they never type-check in CI, or silently disagree with local gates.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (pyrefly branch); `docs/testing.md` (single-file mode note); this repo’s `.github/workflows/ci.yml` / `release.yml` (`--project-excludes 'tests/**' 'scripts/**'`); `[tool.pyrefly]` in root `pyproject.toml`.

**Traps:** Do not hardcode this repo’s excludes for every target — read the target’s pyrefly config or a `[tool.repo-ops.verify]` override. `mypy` path already uses `uv run mypy .` and has its own config-file behaviour; keep the two gates honest rather than forcing identical flags. Prefer failing closed when the target’s intended scope cannot be determined.

**Acceptance:** Fast-path pyrefly verify on a repo with configured excludes matches that repo’s CI/type-check scope; documented in usage/testing; no false verify fails on excluded trees for standard pyrefly layouts.

### 5.3 CONTRIBUTING duplicate “Git for agents” blurb

`CONTRIBUTING.md` repeats the same “Commit tooling and git routing are fleet canon…” sentence twice in § Git for agents.

**Refs:** `CONTRIBUTING.md` lines ~18–21.

**Traps:** Pure doc hygiene; keep a single pointer to fleet CLAUDE.md and the repo-local verification note.

**Acceptance:** Section states the fleet pointer once; no duplicated paragraph.
