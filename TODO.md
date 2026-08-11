# TODO — follow-on / carry-forward

Backlog only. When an item ships, **delete its section** (do not leave "done" / "shipped" markers). Do not re-add work already tracked below.

---

## Round 1 — Documented gaps and open issues

### 1.1 PyPI OIDC trusted publishing (operator hold)

Enable PyPI publish for the `packages/repo-ops` wheel once the trusted-publisher claim is configured. Tracked as open issue [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124).

**Refs:** `.github/workflows/release.yml` (`publish-pypi` job, `environment: pypi`); `AGENTS.md` § Cutting a release; `docs/installation.md` (“After PyPI publish”).

**Traps:** Failure mode is `invalid-publisher` / “valid token, but no corresponding publisher” for claims `sub: repo:Rethunk-Tech/repo-ops:environment:pypi`. That is a **PyPI pending-publisher / claim mismatch**, not a CI defect — do not re-diagnose the workflow. Issue title/body cite the current release line (`v0.22.1` as of Wave 20); bump that note when the release line moves.

**Acceptance:** A `v*` tag run publishes to PyPI; `pip install repo-ops` / `uv tool install repo-ops` from PyPI matches the GitHub Release version; [#124](https://github.com/Rethunk-Tech/repo-ops/issues/124) closed with a verified publish log.

---

## Round 30 — Wave 30 audit carry-forwards

### 30.1 Fleet config e2e: Yarn / Bun / Deno manager paths

`tests/test_config_e2e.py` fleet harness covers pnpm, npm, container, pre-commit, and submodule per-checkout readers. Unit coverage for yarn/bun/pnpm update-latest/targeted-bump and npm targeted-bump lives in `tests/test_js_manager_checkout_flags.py` (mocked `load_repo_config`). Deno still has direct helper coverage only. Fleet-path e2e for yarn/bun/deno remains deferred.

**Refs:** `tests/test_config_e2e.py`; `tests/test_js_manager_checkout_flags.py`; `repo_ops_js_yarn.yarn`; `repo_ops_js_bun.bun`; `repo_ops_js.deno`.

**Acceptance:** Fleet e2e asserts divergent per-checkout resolution for yarn and bun update-latest/targeted-bump (and deno via fleet path, not only direct helpers).

---

## Round 36 — Wave 36 audit carry-forwards

### 36.1 npm/pnpm apply-level ghost defer

Yarn/bun fold blocked initial `assert_targets_resolved` into targeted-bump retry via `unresolved_targets`. `apply_npm` / `apply_pnpm` still raise unconditionally when `changes` exist and a pin blocks — no defer when `--*-targeted-bump` is on.

**Refs:** `repo_ops_js_npm.npm`; `repo_ops_js_pnpm.pnpm`; yarn/bun `_fold_blocked_into_behind` / `unresolved_targets` call sites.

**Acceptance:** With targeted-bump enabled, npm/pnpm apply defer ghost-blocked targets into `targeted_bump_behind` instead of failing the workspace on the first guard.

### 36.2 Bun integration: targeted-bump releases unheld pin

`tests/integration/test_bun_ghost_bump.py` covers raise-without-targeted-bump only. Yarn has `test_yarn_apply_targeted_bump_releases_unheld_pin_instead_of_raising`. Unit coverage for bun defer/release landed in wave36; fleet/integration path still open.

**Refs:** `tests/integration/test_bun_ghost_bump.py`; `tests/integration/test_yarn_ghost_bump.py`.

**Acceptance:** Integration test: bun apply with targeted-bump releases an unheld overrides pin and advances instead of raising.

### 36.3 Deno targeted-bump unheld-pin parity

npm/pnpm/bun/yarn hold tests now cover `release_unheld_pin` wiring. Deno has no analogous holds/release-unheld targeted-bump tests.

**Refs:** `repo_ops_js.deno`; `tests/test_*_holds.py` yarn/npm/pnpm/bun patterns.

**Acceptance:** Deno targeted-bump releases unheld pins and keeps held pins (unit tests mirroring other JS managers).

### 36.4 Dependabot JSON `excluded` key consistency

Dependabot JSON uses `summary.excluded_repos`; plan/audit/pushed use `summary.excluded`. Pre-existing schema drift.

**Refs:** `tests/test_dependabot_sweep.py`; `repo_ops_dependabot.dependabot`; `repo_ops_upgrade.report.build_run_summary`.

**Acceptance:** One canonical JSON field name for excluded-repo counts across subcommands (with migration note if renaming).

### 36.5 Docs: bun remediations use `bunx`

CHANGELOG [Unreleased] documents `pm_exec_prefix("bun")` → `bunx`; `docs/usage.md` is silent.

**Refs:** `docs/usage.md`; `CHANGELOG.md` [Unreleased]; `repo_ops_js.workspaces.pm_exec_prefix`.

**Acceptance:** Usage docs state bun remediations execute via `bunx`, not `bun run`.
