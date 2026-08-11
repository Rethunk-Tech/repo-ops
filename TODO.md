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

### 36.2 Bun integration: targeted-bump releases unheld pin

`tests/integration/test_bun_ghost_bump.py` covers raise-without-targeted-bump only. Yarn has `test_yarn_apply_targeted_bump_releases_unheld_pin_instead_of_raising`. Unit coverage for bun defer/release landed in wave36; fleet/integration path still open.

**Refs:** `tests/integration/test_bun_ghost_bump.py`; `tests/integration/test_yarn_ghost_bump.py`.

**Acceptance:** Integration test: bun apply with targeted-bump releases an unheld overrides pin and advances instead of raising.

---

## Round 37 — Wave 37 audit carry-forwards

### 37.1 Deno apply-level ghost defer parity

npm/pnpm/yarn/bun fold blocked initial `assert_targets_resolved` into targeted-bump retry when `--*-targeted-bump` is on. `apply_deno` still hard-fails ghost before `targeted_bump_behind`.

**Refs:** `repo_ops_js.deno` `apply_deno`; npm/pnpm `_fold_blocked_into_behind` / yarn fold sites.

**Acceptance:** With Deno targeted-bump enabled, ghost-blocked targets defer into `targeted_bump_behind` instead of failing the workspace on the first guard.

### 37.2 Deno pin-release: `jsr:` identifiers and prefix matching

Unheld-pin release / package match coverage today exercises `npm:`-style import-map pins. `_deno_package_matches` only strips the `npm:` prefix; `jsr:` pins lack release/hold parity tests.

**Refs:** `repo_ops_js.deno` `_release_unheld_deno_pin` / `_deno_package_matches`; `tests/test_deno_holds.py`.

**Acceptance:** `jsr:` (and any other supported Deno registry prefix) unheld pins release and held pins stay, with unit coverage mirroring the npm-pin cases.
