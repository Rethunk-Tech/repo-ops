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

**Traps:** Deno’s updater is `deno outdated --update` (± `--latest`); a targeted path must snapshot/restore `deno.lock` + import maps like the JS managers. Ghost-bump guard is best-effort when `node_modules` is absent — targeted bump must not assume npm layout. Do not fold this into `--latest` implicitly (same trap as other `--*-targeted-bump` flags). When this lands, fold it into `--full` (Wave 17 shipped the JS/digest umbrella without Deno).

**Acceptance:** `--deno-targeted-bump` (and config/env twin) moves still-behind direct deps after the bulk update, reverts on no-advance/install failure, appears in the full-coverage sweep docs beside the other four flags, and is enabled by `--full`.

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

## Round 4 — Hygiene carry-forward

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

## Round 6 — Audit / security parity

### 6.1 Manager-aware Python `audit` (stop forcing `py-uv`)

`_detect_workspaces` labels any `pyproject.toml` / `uv.lock` tree as `py-uv` and runs `uv run pip-audit`. Poetry / PDM / pipenv / hatch / pip-tools layouts are misrouted or silently omitted (`requirements.in`+`requirements.txt` trees need a pyproject/uv.lock gate today).

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`_detect_workspaces`, `_audit_py_uv`); `docs/usage.md` § audit ecosystem table; plan/apply discovery in `packages/repo-ops-upgrade/src/repo_ops_upgrade/discovery.py`.

**Traps:** `uv run pip-audit` on a non-uv project can fail closed as “skipped” while the workspace looks audited in the roll-up. Prefer manager-native export + audit (or documented pip-audit-on-exported-reqs) over pretending everything is uv. Reuse upgrade discovery kinds — do not grow a second incompatible walker forever (same class as Cargo child suppression in 1.3).

**Acceptance:** Audit detects the real Python manager; poetry/PDM/pipenv/hatch/pip-tools repos are included with explicit ecosystem labels; wrong-manager skips are visible in JSON (`skip_reason`), not false-clean `ok` rows.

### 6.2 Deno vulnerability audit path

Deno has plan/apply/verify and remediations, but `audit_workspace` stops at go / node / py-uv / rust — fleet `audit` never sees Deno CVEs.

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`audit_workspace`); `packages/repo-ops-js/src/repo_ops_js/deno.py`; `packages/repo-ops-upgrade-remediations-deno`; `docs/usage.md` audit table.

**Traps:** Prefer `deno audit` (or the current Deno-documented equivalent) over inventing a third-party scanner. `--fix` must snapshot/restore `deno.lock` + import maps like apply. Missing `deno` on PATH should skip with reason, matching govulncheck/cargo-audit patterns.

**Acceptance:** `repo-ops audit` discovers Deno workspaces, normalizes severities into the shared count schema, and supports `--fix-plan` / `--fix` with reversible lock edits; usage table lists Deno beside the other ecosystems.

---

## Round 7 — Verify depth and pin-move parity

### 7.1 `uv` verify parity with hatch fast gates

After `uv sync --locked`, uv workspaces only optionally run `ruff`. Hatch runs `_verify_hatch_fast` (scripts, ruff, mypy/pyrefly, pytest). Migrating hatch→uv silently drops typecheck/test gates.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (`verify_workspace` uv vs hatch branches, `_verify_hatch_fast`); `docs/usage.md` verify notes.

**Traps:** Share gate helpers rather than copy-paste hatch logic. Respect `[verify] skip` / timeout. Pair with 5.2 so pyrefly scope matches the target’s config when that gate runs.

**Acceptance:** uv workspaces with mypy/pyrefly/pytest (or equivalent scripts) run the same fast gates hatch does after a locked sync; documented in usage.

### 7.2 Poetry / PDM / pipenv verify beyond lock integrity

Poetry verify is `poetry check`; PDM is `pdm sync --clean`; pipenv is `pipenv verify`. Lock integrity can pass while lint/typecheck/tests never run — weaker than node/go/uv-hatch paths.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py` (poetry/pdm/pipenv branches); manager packages under `packages/repo-ops-py-{poetry,pdm,pipenv}`.

**Traps:** Do not invent manager-specific mini-CI — prefer configured project scripts / known tools already on the hatch/uv path. Missing binaries should fail closed with a clear reason, not skip silently. Keep timeouts consistent with other verify gates.

**Acceptance:** Each manager runs a documented gate stack (lock sync/check + lint/typecheck/test when configured); failures block apply like other ecosystems; usage lists the stack per manager.

### 7.3 Opt-in SHA refresh for pre-commit and submodule pins

Actions/container already have `--actions-refresh-sha` / `--container-refresh-sha`. Pre-commit SHA `rev:` and submodule SHA-only HEADs stay permanent report-only with no move path — same “why didn’t apply move this?” wall operators hit before those flags existed.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py` (SHA rev → report-only); `packages/repo-ops-upgrade/src/repo_ops_upgrade/submodules.py` (SHA-only HEAD → report-only); contrast `actions.py` / `repo-ops-container`; `AGENTS.md` full-coverage sweep table.

**Traps:** Refresh must stay digest-within-intent (same hook repo / same submodule URL), not a silent major leap. Compose into the eventual `--full` umbrella (3.1) once named. Holds (1.2) should define ceiling behaviour before aggressive refresh. Container SHA-without-tag remains a separate harder case — do not pretend a tag-less digest has a refresh target.

**Acceptance:** Opt-in flags (e.g. `--precommit-refresh-sha`, `--submodule-refresh-sha`) advance those pins when resolvable; docs/AGENTS list them beside the existing refresh flags; without the flags, behaviour stays report-only.

---

## Round 8 — Fleet DX / cross-subcommand gaps

### 8.3 `pushed` remote/API fallback (local reflog blind spot)

`repo-ops pushed` only counts this-machine `refs/remotes/*` reflog “update by push” entries. Fresh clones, CI runners, and pushes from another workstation report zero even when GitHub has activity.

**Refs:** `packages/repo-ops-pushed/src/repo_ops_pushed/audit.py` (module docstring, `PUSH_RE`); `docs/usage.md` pushed blurb.

**Traps:** Document the limitation even if code stays local-only. Any `--source=gh` path needs authenticated `gh`, rate-limit discipline, and must not invent pushes the API cannot attribute. Reflog expiry under gc is the same class of false zero.

**Acceptance:** Usage calls out the machine-local limitation prominently; optional remote source (or an explicit “unsupported on fresh clones” exit/warning) so operators do not trust empty tables as “no pushes”.

### 8.4 Actions org-policy block/warn mode

`--actions-org-policy` records `status="policy"` rows but “never blocks pin bumps”. Unlike `--actions-lint-level block|warn|off`, policy violations cannot stop apply from rewriting disallowed actions.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py` (~policy check / “report-only; never blocks”); `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--actions-org-policy`, `--actions-lint-level`).

**Traps:** Mirror the lint-level pattern rather than a one-off boolean. Default must stay report-only for back-compat. Policy fetch failures stay non-fatal (already best-effort).

**Acceptance:** A documented level (`block`/`warn`/`report`) can skip or warn on violating pin rewrites; JSON distinguishes policy-report vs policy-blocked; default remains today’s report-only behaviour.

### 8.5 Dependabot `--scaffold` ecosystem gaps

Scaffold emits coarse `pip` for any Python indicator, `npm` for any Node lock (including bun), and omits `deno` / `pre-commit` entirely — generated configs under-serve modern fleet layouts.

**Refs:** `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (`detect_ecosystems`, `_NODE_INDICATORS`, `_PY_INDICATORS`); Dependabot docs for valid `package-ecosystem` values; distinct from supersession precision (4.3; poetry/pdm/pipenv high-confidence shipped Wave 18).

**Traps:** Only emit ecosystems Dependabot actually supports — do not invent fake ecosystem strings. Bun may still map to `npm` upstream; document that. Keep prune-by-regeneration behaviour (`--write` replaces the file).

**Acceptance:** Scaffold detects deno and pre-commit where indicators exist; Python/manager notes in docs list scaffold coverage vs supersession coverage separately; dry-run output matches what `--write` would emit.

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: per-checkout config besides commit-agent model/provider

`run_upgrade` still loads `load_repo_config(Path.cwd())` once and injects via `apply_config_to_env`. Wave 17/18 made `commit_agent_model` and `commit_agent_provider` resolve per `bundle.git_repo` at draft time; other config keys (exclude, jobs, …) still ignore each target checkout’s `config.toml` on multi-root apply.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`run_upgrade` config load); `repo_ops_core.config.apply_config_to_env` / `resolve_setting`; `commit_agent_cursor._resolve_agent_model` / `commit_agent_providers.commit_agent_provider_name` (per-repo precedent).

**Traps:** Do not double-apply env injection mid-flight in a way that leaks one repo’s settings into the next. Prefer resolve-at-use (like commit-agent model/provider) over mutating process-wide env per checkout.

**Acceptance:** Documented settings that are repo-scoped honor each checkout’s config on fleet `plan`/`apply`; global + env + CLI precedence unchanged.

---

## Round 11 — Wave 19 audit carry-forwards

### 11.1 Doctor `probe_skill_drift` dangling-symlink blind spot

`install-skill --check` now routes symlinks (including dangling) through digest/drift checks. Doctor still gates on `path.exists()` before probing, so a dangling skill symlink is skipped rather than reported in `skill_drift`.

**Refs:** `packages/repo-ops-doctor/src/repo_ops_doctor/doctor.py` (`probe_skill_drift`); contrast `packages/repo-ops/src/repo_ops/install_skill.py` (`check_installed_skill` / `--check` path).

**Acceptance:** Doctor reports dangling or broken skill symlinks as drift (human warn + JSON `skill_drift`) with the same semantics as `install-skill --check`.

### 11.2 `pushed` CLI integration test for exclude human output

Exclude filtering is covered at `collect_results` unit level; there is no `main([... --exclude ...])` capsys assertion for the `Excluded repos:` line.

**Refs:** `tests/test_pushed.py` (`test_collect_results_excludes_repositories`); `packages/repo-ops-pushed/src/repo_ops_pushed/audit.py` human exclude footer.

**Acceptance:** A focused CLI test asserts exclude patterns skip repos and print the excluded count on stderr/stdout as implemented.

### 11.3 Fixture test for release CHANGELOG notes extraction

Release notes awk now matches dated Keep a Changelog headings, but only validates on the next `v*` tag push. A fixture CHANGELOG + shell/Python extraction check would catch heading-format regressions pre-tag.

**Refs:** `.github/workflows/release.yml` Prepare release notes step; `CHANGELOG.md` `## [X.Y.Z] - <date>` form.

**Acceptance:** A non-integration test (or workflow-local self-check) proves extraction yields a non-empty body for a dated section and fails closed on a missing version.
