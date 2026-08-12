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

---

## Round 38 — Packaging, holds, and SBOM shape

### 38.1 Declare `tomli` for Python 3.10 runtime (not only pytest)

`repo_ops_core.filesystem` and `repo_ops_core.config` import `tomli` when stdlib `tomllib` is absent. `repo_ops_lockfiles.license_enrich` does the same for on-disk `Cargo.toml`. Neither `packages/repo-ops-core` nor `packages/repo-ops-lockfiles` declares `tomli`; the lockfile only pulls it in as a pytest extra (`python_full_version < '3.11'`). A 3.10 `uv tool install` / wheel install without the workspace `dev` group therefore hits `ModuleNotFoundError` at `filesystem` import (hard fail), while config's `_load_toml` path is written to skip. Docs still say “skipped with a warning on 3.10 unless `tomli` is installed.”

**Refs:** `packages/repo-ops-core/src/repo_ops_core/filesystem.py`; `packages/repo-ops-core/src/repo_ops_core/config.py`; `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/license_enrich.py`; `packages/repo-ops-core/pyproject.toml`; `packages/repo-ops-lockfiles/pyproject.toml`; `docs/usage.md` (Settings reference, 3.10 note).

**Traps:** Marker must be `python_version < "3.11"` so 3.11+ keeps stdlib-only. Do not rely on pytest's transitive `tomli`. `filesystem.read_toml_file` currently assumes the import succeeded — either declare the dep or match config's skip-and-warn. Align `license_enrich` with the same helper rather than a third copy of the try/import.

**Acceptance:** `repo-ops-core` (and lockfiles if it keeps a local import) depends on `tomli` under the 3.10 marker; a clean 3.10 install without pytest still loads config, `read_toml_file`, and Cargo license enrichment. Import of `filesystem` does not raise when `tomli` is present; when it is absent, behavior matches the documented skip/warn rather than crashing at import.

### 38.2 Hold clamp must not rewrite Python manifests

Post-apply hold enforcement for uv/hatch (`uv add <pkg><=ceiling>`), poetry (`poetry add …`), and pdm (`pdm add …`) writes a new constraint into `pyproject.toml` / the manager manifest. pip-tools already clamps via an ephemeral constraints file and does **not** rewrite `requirements.in`. A routine `--latest` apply that then clamps a hold therefore leaves a permanent manifest pin the operator never asked to commit. uv documents `uv lock --upgrade-package <pkg>==<version>` as lock-only; that is the right shape.

**Refs:** `packages/repo-ops-py/src/repo_ops_py/uv_lock.py` `_enforce_version_ceilings`; `packages/repo-ops-py-poetry/src/repo_ops_py_poetry/poetry.py`; `packages/repo-ops-py-pdm/src/repo_ops_py_pdm/pdm.py`; `packages/repo-ops-py-pip-tools/src/repo_ops_py_pip_tools/pip_tools.py`; `packages/repo-ops-audit/src/repo_ops_audit/audit.py` `_apply_py_uv_targeted` (same `uv add` pattern for `--fix`).

**Traps:** `uv add` / `poetry add` / `pdm add` are the current tests' expected argv — changing the command is a behavior change, not a rename. Audit `--fix` pinning to an exact safe version *does* belong in the manifest; hold clamp does not. Do not use `uv add` for holds even with `--frozen`. Hatch delegates to `uv_lock` — one fix covers both.

**Acceptance:** Hold clamp for uv/hatch/poetry/pdm updates the lockfile (and only the lockfile) to a version `<= max_version`. `pyproject.toml` / Poetry/PDM dependency tables are byte-identical before vs after a hold clamp. Audit `--fix` exact pins may still rewrite the manifest. pip-tools remains constraints-file-only.

### 38.3 CycloneDX emit: 1.4 `metadata.tools[]` vs current spec

`emit_cyclonedx` hard-codes `specVersion: "1.4"` and the 1.4 `metadata.tools` array of `{vendor,name,version}`. CycloneDX 1.5+ (library default now includes 1.6/1.7) moved tools to `metadata.tools.components` / `.services`. GitHub dependency-graph and several OSV consumers prefer ≥1.5 JSON. SPDX path is separate and can stay.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/sbom.py` `emit_cyclonedx`; `docs/usage.md` § `sbom`.

**Traps:** 1.5+ rejects the old `tools[]` object shape if `specVersion` is bumped without migrating the field. Keep 1.4 as an explicit `--format`/`spec` choice if anything already consumes the current document. Do not pull `cyclonedx-python-lib` unless emit complexity actually needs the model; a targeted dict-shape bump is enough.

**Acceptance:** Default CycloneDX JSON validates as ≥1.5 (tools in the 1.5+ location). Existing 1.4 consumers either still work via an opt-in spec flag or are documented as dropped. `--enrich-licenses` expressions still serialize under the chosen spec.

---

## Round 39 — Upgrade-mode and discovery gaps

### 39.1 Python managers: no `--latest` / targeted-bump

`--full` / `--latest` plus `--*-targeted-bump` close JS/Deno `behind_latest` gaps. poetry, pdm, pipenv, and pip-tools have no equivalent: `poetry update` / `pdm update` / `pipenv update` stay inside existing constraints, and usage.md marks pdm/pipenv aggressive mode as “—”. uv already runs `uv lock --upgrade` (constraint-respecting, not a JS-style latest umbrella). Operators on Poetry/PDM fleets therefore under-report the same class of drift `--full` is meant to catch.

**Refs:** `docs/usage.md` § Ecosystem upgrade modes; `packages/repo-ops-py-poetry/src/repo_ops_py_poetry/poetry.py`; `packages/repo-ops-py-pdm/src/repo_ops_py_pdm/pdm.py`; `packages/repo-ops-py-pipenv/src/repo_ops_py_pipenv/pipenv.py`; `packages/repo-ops-py-pip-tools/src/repo_ops_py_pip_tools/pip_tools.py`; `packages/repo-ops-core/src/repo_ops_core/cli.py` (flag wiring for JS targeted-bump).

**Traps:** Do not silently rewrite `^1.2` to `^2` in `pyproject.toml` without an opt-in flag (same contract as `--yarn-targeted-bump`). pip-tools `requirements.in` pins are the constraint — a “latest” mode must be explicit. pipenv must not create a venv (existing check is read-only via `pipenv --venv`). Hold ceilings from 38.2 must apply to any new bump path.

**Acceptance:** Opt-in flags (or a documented “Python has no latest mode” sentence that is true for all four) exist for poetry/pdm/pipenv/pip-tools. With the flag, a direct dep whose lock is behind a newer published version that requires a constraint bump is either moved (revert-on-failure, like JS targeted-bump) or listed in `behind_latest` instead of `[ok]`. `--full` either includes those flags or the docs say Python constraint bumps stay manual.

### 39.2 pnpm `catalog:` versions in `pnpm-workspace.yaml`

Overrides audit treats `catalog:` protocol pins as non-stale (`repo_ops_js.overrides`, #50). `repo-ops-js-pnpm` never reads or bumps the `catalogs:` / `catalog:` tables in `pnpm-workspace.yaml`. `pnpm update --latest` can leave catalog-defined versions untouched while member `package.json` still says `catalog:`. Fleet repos using pnpm catalogs then look current in plan/apply.

**Refs:** `packages/repo-ops-js/src/repo_ops_js/overrides.py` (`catalog:` skip); `packages/repo-ops-js-pnpm/src/repo_ops_js_pnpm/`; pnpm workspace catalog docs.

**Traps:** Default vs named catalogs (`catalog:` vs `catalog:foo`). Do not rewrite `workspace:` / `link:` / `file:` siblings. Catalog bumps are workspace-root files — `apply --commit` scoping must include `pnpm-workspace.yaml` (same class of bug as #153 nested manifests). Holds must apply to catalog package names.

**Acceptance:** Plan/apply can advance a catalog entry when `--pnpm-targeted-bump` / `--latest` says to, with snapshot/restore if `pnpm install` fails. Catalog-only drift shows in `behind_latest` when not actioned. `pnpm-workspace.yaml` is in the commit path when the catalog table changed.

### 39.3 Opt-in Cargo.toml constraint bump (Rust majors still default-off)

`cargo update` only refreshes within existing `Cargo.toml` reqs; `--latest` is documented as a no-op for rust. Direct-dep major/minor constraint edits stay hand work. That is honest, but there is still no opt-in analog to `--yarn-targeted-bump` (e.g. `cargo update -p crate --precise` already used for holds/audit, plus a manifest req bump).

**Refs:** `packages/repo-ops-rust/src/repo_ops_rust/rust.py`; `docs/usage.md` rust row; `AGENTS.md` (`cargo update` only); `packages/repo-ops-audit` cargo `--precise` path.

**Traps:** Default must remain “no Cargo.toml edits.” Do not take a hard dependency on `cargo-edit` if `cargo update --precise` + a small toml edit suffices. Workspace inheritance (`workspace.dependencies`) and path/git crates must be skipped. Yanked crates.io versions (already fetched in `_cargo_registry_versions`).

**Acceptance:** Without a new flag, rust behavior is unchanged. With the flag, a direct crates.io dep whose latest published version is outside the current req can be bumped in `Cargo.toml` + lock, reverted on `cargo update` failure, and listed in `behind_latest` when skipped. Path/git/workspace crates never get a registry bump.

### 39.4 `doctor` does not probe `ruff`

Verify runs `ruff check` when ruff is a dev dependency (`repo_ops_upgrade.verify`). `repo-ops doctor`’s static binary table includes pyrefly, hatch, pip-audit, actionlint, and cargo-audit, but not `ruff`. A missing ruff only shows up mid-apply as a verify failure.

**Refs:** `packages/repo-ops-doctor/src/repo_ops_doctor/doctor.py` `_BINARIES`; `packages/repo-ops-upgrade/src/repo_ops_upgrade/verify.py`; `docs/usage.md` § `doctor`; `AGENTS.md` doctor probe list.

**Traps:** Doctor is “binaries we shell out to,” not “every possible verify tool.” `mypy` was dropped from this repo’s verify in favor of pyrefly but verify still has a mypy branch — do not add mypy to doctor unless it is still invoked. Keep ecosystem label `python`.

**Acceptance:** `repo-ops doctor` reports ruff found/missing/version like the other Python tools; `--strict` fails when ruff is absent. HUMANS/usage/AGENTS doctor lists include it. No duplicate probe for `ruff format`.

### 39.5 SBOM drops reusable GitHub Actions workflows

`collect` emits `pkg:github/owner/repo@ref` only when `action.count("/") == 1`. Reusable workflows (`owner/repo/.github/workflows/ci.yml@v1`) and nested action paths (`owner/repo/subdir`) are skipped. Those refs are still upgraded by the Actions pin rewriter and counted by inventory.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/sbom.py` (workflow walk, `count("/") != 1` skip); `packages/repo-ops-upgrade/src/repo_ops_upgrade/workflow_uses.py`; `packages/repo-ops-inventory/src/repo_ops_inventory/counting.py` `count_actions`.

**Traps:** PURL for a reusable workflow is not `pkg:github/owner/repo@ref` with a lost path — keep the extra path in `name` or qualifier so two workflows in one repo do not collapse. Local `./` and docker:// uses stay out. License enrichment for Actions is already limited.

**Acceptance:** SBOM components include reusable-workflow and nested-action `uses:` pins with distinct purls/names. Local and docker uses remain omitted. Inventory/plan action counts and SBOM component counts agree on those refs.
