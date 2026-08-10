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

## Round 7 — Verify depth and pin-move parity

### 7.3 Opt-in SHA refresh for pre-commit and submodule pins

Actions/container already have `--actions-refresh-sha` / `--container-refresh-sha`. Pre-commit SHA `rev:` and submodule SHA-only HEADs stay permanent report-only with no move path — same “why didn’t apply move this?” wall operators hit before those flags existed.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/precommit.py` (SHA rev → report-only); `packages/repo-ops-upgrade/src/repo_ops_upgrade/submodules.py` (SHA-only HEAD → report-only); contrast `actions.py` / `repo-ops-container`; `AGENTS.md` full-coverage sweep table.

**Traps:** Refresh must stay digest-within-intent (same hook repo / same submodule URL), not a silent major leap. Compose into the `--full` umbrella once named. Holds (1.2) should define ceiling behaviour before aggressive refresh. Container SHA-without-tag remains a separate harder case — do not pretend a tag-less digest has a refresh target.

**Acceptance:** Opt-in flags (e.g. `--precommit-refresh-sha`, `--submodule-refresh-sha`) advance those pins when resolvable; docs/AGENTS list them beside the existing refresh flags; without the flags, behaviour stays report-only.

---

## Round 9 — Wave 17 follow-ons

### 9.1 Fleet apply: remaining per-checkout config keys

Fleet `plan`/`apply` now resolve per checkout: exclusions, Actions lint/policy levels, and commit-agent model/provider (via ambient-env snapshot so injected global config cannot beat per-repo). Other documented keys (`jobs`, `go_upgrade_mode`, timeouts, yarn concurrency, verify profile wiring beyond existing `verify.py` path, etc.) remain invocation/global-scoped.

**Refs:** `packages/repo-ops-upgrade/src/repo_ops_upgrade/run.py` (`_ambient_env`, per-bundle exclude / actions resolve); `repo_ops_core.config.resolve_setting` (`env=` snapshot); `docs/usage.md` config section.

**Traps:** Do not mutate process-wide env per checkout mid-flight. Prefer resolve-at-use with ambient snapshot over `apply_config_to_env` swaps.

**Acceptance:** Remaining documented repo-scoped settings honor each checkout’s config on fleet runs; CLI + caller env precedence unchanged; docs list which keys are per-checkout vs invocation-scoped.

---

## Round 23 — Wave 23 follow-ons

### 23.3 Enrich: explicit `--enrich-licenses` cannot override LOCAL env

When `REPO_OPS_ENRICH_LICENSES_LOCAL=1`, `allow_network` stays false even if `--enrich-licenses` is also passed (local wins). Documented; no CLI force-network override.

**Refs:** `license_policy.main`; `sbom.main`; `docs/usage.md` enrich bullets.

**Acceptance:** Optional `--enrich-licenses-network` (or equivalent) that forces network despite LOCAL env, with a regression test; or keep local-wins and close this as wontfix once operators confirm.

### 23.4 Deno targeted guard: lockfile-based verification when node_modules absent

`assert_targets_resolved` skips when the resolver returns no `node_modules` map; Deno `npm:` targeted bumps can therefore pass the ghost-bump guard with zero on-disk package verification.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/guards.py` (`assert_targets_resolved`); `packages/repo-ops-js/src/repo_ops_js/deno.py` (`targeted_bump_behind`).

**Acceptance:** Successful Deno targeted bumps verify via lockfile (or equivalent) when `node_modules` is absent; or document the skip as an explicit limitation with a regression that pins the behaviour.

### 23.5 License policy BSD needle case sensitivity

`license_policy._classify_license_text` matches the BSD-3 endorse/promote needle case-sensitively against lowercase text in some paths; ALL-CAPS LICENSE files can miss BSD-3 while `license_enrich.classify_license_text` (normalized path) still distinguishes.

**Refs:** `license_policy._classify_license_text` / `_LICENSE_TEXT_SIGNATURES`; `license_enrich.classify_license_text`.

**Acceptance:** Both classifiers agree on case-variant BSD-2/BSD-3 LICENSE text; regression covers ALL-CAPS endorse clause.
