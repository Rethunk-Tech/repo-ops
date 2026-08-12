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

## Round 38 — Packaging, holds, and SBOM shape

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

**Traps:** Do not silently rewrite `^1.2` to `^2` in `pyproject.toml` without an opt-in flag (same contract as `--yarn-targeted-bump`). pip-tools `requirements.in` pins are the constraint — a “latest” mode must be explicit. pipenv must not create a venv (existing check is read-only via `pipenv --venv`). Hold clamp is lock-only (uv `lock --upgrade-package`; poetry/pdm/pipenv snapshot-restore the manifest) and must apply to any new bump path.

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

---

## Round 40 — Wave 40 audit carry-forwards

### 40.1 Collapse config `_TOMLLIB_AVAILABLE` onto `TOML_AVAILABLE`

`config.py` imports `TOML_AVAILABLE` from filesystem then aliases `_TOMLLIB_AVAILABLE = TOML_AVAILABLE`. Tests patch the alias (`tests/test_core_config.py`).

**Refs:** `packages/repo-ops-core/src/repo_ops_core/config.py`; `packages/repo-ops-core/src/repo_ops_core/filesystem.py`; `tests/test_core_config.py`.

**Acceptance:** One name. Tests patch that name. Behavior of skip-and-warn on 3.10 without tomli is unchanged.

### 40.2 Single `_hold_ceiling_spec` helper

Identical pad-to-three-component `<=` spec lives in `uv_lock.py`, `poetry.py`, `pdm.py`, `pipenv.py`, and `pip_tools.py`.

**Refs:** those five modules; `repo_ops_core.holds` or `repo_ops_core.version_utils`.

**Acceptance:** One helper; all five managers call it. Ceiling padding semantics unchanged.

### 40.3 `filesystem` tomli import should catch `ImportError`

The tomli fallback only catches `ModuleNotFoundError`. A broken tomli install that raises a broader `ImportError` still crashes `import repo_ops_core.filesystem`.

**Refs:** `packages/repo-ops-core/src/repo_ops_core/filesystem.py`.

**Traps:** Do not swallow `KeyboardInterrupt`. Keep skip-and-warn when the parser is absent.

**Acceptance:** `import repo_ops_core.filesystem` succeeds when tomli exists but fails to import; `TOML_AVAILABLE` is false and `read_toml_file` returns None.

### 40.4 Doctor test does not lock `pyrefly` before `ruff`

`BinarySpec("ruff", "python")` sits after `pyrefly` in `_BINARIES`. JSON tests include both names in a set, not order.

**Refs:** `packages/repo-ops-doctor/src/repo_ops_doctor/doctor.py`; `tests/test_doctor.py`.

**Acceptance:** A test asserts `pyrefly` precedes `ruff` in the probe table / JSON binaries list.

### 40.5 SBOM local `uses:` that do not start with `.`

Workflow collect skips `action.startswith(".")` and `docker://`. A `../.github/workflows/foo.yml` local ref would be emitted as a github component.

**Refs:** `packages/repo-ops-lockfiles/src/repo_ops_lockfiles/sbom.py` workflow walk.

**Acceptance:** Local-path `uses:` (relative, including `../`) stay omitted. Marketplace, nested, and reusable-workflow refs stay included.
