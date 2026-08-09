# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

### 1.2 Package holds for pin ecosystems (container / Actions / pre-commit / submodules)

`[[hold]]` rules are documented as not yet consulted by container, GitHub Actions, submodule, and pre-commit checkers (same schema, no enforcement). Rust and Deno are also outside the current enforcement matrix.

**Refs:** `docs/usage.md` § Package holds (“Not yet wired…”); `repo_ops_core.holds`; checkers in `packages/repo-ops-container`, `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py`, `precommit.py`, `submodules.py`; `packages/repo-ops-rust`, `packages/repo-ops-js/src/repo_ops_js/deno.py`.

**Traps:** Actions/container “versions” are often digests or floating majors (`v4` vs SHA); a semver ceiling must define digest-refresh / unpin behaviour explicitly. Audit `--fix` advisory overrides must continue to win over holds (parity with JS/Python). Go already documents same-major-only ceilings — do not invent cross-major Go holds.

**Acceptance:** A hold on an Actions pin, container tag/digest, pre-commit `rev`, or submodule ref caps plan/apply the same way JS/Go/Python do; docs list each ecosystem; expired holds still skip without rewriting config.

---

## Round 2 — Ecosystem parity

### 2.1 Deno `--deno-targeted-bump` (JS targeted-bump parity)

Deno reports `behind_latest` and honors `--latest` / `REPO_OPS_DENO_UPDATE_LATEST`, but unlike yarn/npm/pnpm/bun there is no reversible per-package targeted-bump fallback to *move* remaining gaps.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/deno.py` (`check_deno` / `apply_deno`); JS siblings `targeted_bump_behind` in `repo-ops-js-{yarn,npm,pnpm,bun}`; CLI flags in `packages/repo-ops-core/src/repo_ops_core/cli.py`; `AGENTS.md` full-coverage sweep table.

**Traps:** Deno’s updater is `deno outdated --update` (± `--latest`); a targeted path must snapshot/restore `deno.lock` + import maps like the JS managers. Ghost-bump guard is best-effort when `node_modules` is absent — targeted bump must not assume npm layout. Do not fold this into `--latest` implicitly (same trap as other `--*-targeted-bump` flags). When this lands, fold it into `--full` (Wave 17 shipped the JS/digest umbrella without Deno).

**Acceptance:** `--deno-targeted-bump` (and config/env twin) moves still-behind direct deps after the bulk update, reverts on no-advance/install failure, appears in the full-coverage sweep docs beside the other four flags, and is enabled by `--full`.

### 2.4 Rust upgrade-mode honesty vs aggressive bumps

`--latest` does not change Cargo behaviour (`cargo update` stays within existing constraints). Operators cannot opt into conservative vs aggressive Rust bumps the way Go (`patch`/`explicit`/`mvs`) or container cross-major gates work. Documented as intentional — still a fleet gap when Rust majors need a deliberate path (e.g. `cargo upgrade` / manifest edits).

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `docs/usage.md` ecosystem upgrade-modes table; audit remediations already use `cargo update -p … --precise`.

**Traps:** Inventing a fake `--latest` that only re-runs `cargo update` would lie. Manifest-editing upgrades are a different product surface from lockfile refresh — keep the boundary explicit. Holds (1.2) should land before or with any aggressive Rust mode.

**Acceptance:** Either a real opt-in Rust upgrade mode (documented, gated, tested on manifest+lock) **or** a single canonical doc/AGENTS note that Rust majors are out-of-scope for repo-ops and must be done by hand — no half-mode.

---

## Round 6 — Audit / security parity

### 6.1 Manager-aware Python `audit` (stop forcing `py-uv`)

`_detect_workspaces` labels any `pyproject.toml` / `uv.lock` tree as `py-uv` and runs `uv run pip-audit`. Poetry / PDM / pipenv / hatch / pip-tools layouts are misrouted or silently omitted (`requirements.in`+`requirements.txt` trees need a pyproject/uv.lock gate today).

**Refs:** `packages/repo-ops-audit/src/repo_ops_audit/audit.py` (`_detect_workspaces`, `_audit_py_uv`); `docs/usage.md` § audit ecosystem table; plan/apply discovery in `packages/repo-ops-upgrade/src/repo_ops_upgrade/discovery.py`.

**Traps:** `uv run pip-audit` on a non-uv project can fail closed as “skipped” while the workspace looks audited in the roll-up. Prefer manager-native export + audit (or documented pip-audit-on-exported-reqs) over pretending everything is uv. Reuse upgrade discovery kinds — do not grow a second incompatible walker forever.

**Acceptance:** Audit detects the real Python manager; poetry/PDM/pipenv/hatch/pip-tools repos are included with explicit ecosystem labels; wrong-manager skips are visible in JSON (`skip_reason`), not false-clean `ok` rows.

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

### 8.4 Actions org-policy block/warn mode

`--actions-org-policy` records `status="policy"` rows but “never blocks pin bumps”. Unlike `--actions-lint-level block|warn|off`, policy violations cannot stop apply from rewriting disallowed actions.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/actions.py` (~policy check / “report-only; never blocks”); `packages/repo-ops-core/src/repo_ops_core/cli.py` (`--actions-org-policy`, `--actions-lint-level`).

**Traps:** Mirror the lint-level pattern rather than a one-off boolean. Default must stay report-only for back-compat. Policy fetch failures stay non-fatal (already best-effort).

**Acceptance:** A documented level (`block`/`warn`/`report`) can skip or warn on violating pin rewrites; JSON distinguishes policy-report vs policy-blocked; default remains today’s report-only behaviour.

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: per-checkout config besides commit-agent model/provider

`run_upgrade` still loads `load_repo_config(Path.cwd())` once and injects via `apply_config_to_env`. Wave 17/18 made `commit_agent_model` and `commit_agent_provider` resolve per `bundle.git_repo` at draft time; other config keys (exclude, jobs, …) still ignore each target checkout’s `config.toml` on multi-root apply.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`run_upgrade` config load); `repo_ops_core.config.apply_config_to_env` / `resolve_setting`; `commit_agent_cursor._resolve_agent_model` / `commit_agent_providers.commit_agent_provider_name` (per-repo precedent).

**Traps:** Do not double-apply env injection mid-flight in a way that leaks one repo’s settings into the next. Prefer resolve-at-use (like commit-agent model/provider) over mutating process-wide env per checkout.

**Acceptance:** Documented settings that are repo-scoped honor each checkout’s config on fleet `plan`/`apply`; global + env + CLI precedence unchanged.

---

## Round 21 — Wave 21 follow-ons

### 21.1 License enrichment: local-only mode + safer text classification

`--enrich-licenses` always sets `allow_network=True`. Air-gapped operators need local METADATA/LICENSE enrichment without registry calls. BSD-family LICENSE text classification can mis-label BSD-3 (and relatives) as `BSD-2-Clause` via a shared needle — prefer `NOASSERTION` on ambiguity.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/license_enrich.py`; `sbom.py` / `license_policy.py` enrich wiring.

**Acceptance:** Opt-in local-only enrichment (flag or env); ambiguous LICENSE text stays unknown rather than a wrong SPDX id.

### 21.2 Dependabot: stop parsing `poetry.lock` via `versions_uv_lock`

`_lockfile_resolved` routes `poetry.lock` through `versions_uv_lock`, which is the wrong format — Poetry high-confidence supersession is unreliable (pre-Wave-21).

**Refs:** `packages/repo-ops-dependabot/src/repo_ops_dependabot/dependabot.py` (~poetry.lock branch); lockfiles poetry parser if any.

**Acceptance:** Poetry locks use a poetry-aware resolver (or explicit low-confidence) so `--close` never high-confidence-closes on a false uv parse.
